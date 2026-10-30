"""Check the preservation requirements and build the journal-backed archive services.

Contract: preservation brief (ADRs 0011-0013) sections 3 (D9, O5), 5.1 and 5.2.
``startup_problems`` lists every unmet requirement at once; the composition root and
``chronikwerk-admin validate-config`` report the same list. ``build_preservation_services``
wires the journal, catalog, store, seal, timestamper, pipeline and scheduler.
"""

from __future__ import annotations

import asyncio
import os
import re
import stat
from collections.abc import Awaitable, Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from asn1crypto import x509 as asn1_x509

from chronikwerk._version import VERSION
from chronikwerk.archiving.capture import CaptureOptions
from chronikwerk.archiving.options import ArchiveWorkflowOptions
from chronikwerk.archiving.pipeline import ArchivePipeline
from chronikwerk.archiving.pipeline_options import PreservationOptions
from chronikwerk.configuration.models import Settings
from chronikwerk.configuration.retention import RetentionPolicy, load_retention_policy
from chronikwerk.configuration.validation import (
    ConfigValidationError,
    ConfigValidationIssue,
    preservation_requirements,
)
from chronikwerk.documents.options import DocumentOptions, TimestampOptions
from chronikwerk.documents.tsa import build_timestamper
from chronikwerk.failures import PermanentError
from chronikwerk.operations.journal import JournalOptions, RequestJournal
from chronikwerk.operations.scheduling import JournaledSchedulingService
from chronikwerk.preservation.catalog import ArchiveCatalog
from chronikwerk.preservation.manifest import Producer
from chronikwerk.preservation.seal import SealOptions, load_trust_anchors, seal_identity_problems
from chronikwerk.storage.archive_fs import FilesystemArchiveStore
from chronikwerk.storage.archive_store import ArchiveStoreError
from chronikwerk.storage.filesystem import remove_tree_within_root
from chronikwerk.timestamps import now_utc
from chronikwerk.zammad.gateway import AsyncZammadClient
from chronikwerk.zammad.transport import MAX_RESPONSE_BODY_BYTES

__all__ = [
    "MAX_METADATA_BYTES",
    "PreservationMaterial",
    "PreservationServices",
    "abandoned_capture_dirs",
    "build_preservation_services",
    "load_preservation_material",
    "startup_problems",
    "sweep_capture_temp",
]

log = structlog.get_logger(__name__)

# Ticket JSON, article list and original emails are read in memory; the gateway refuses
# bodies above its own response cap anyway, so the capture limit matches it.
MAX_METADATA_BYTES = MAX_RESPONSE_BODY_BYTES
_TEMP_DIR_MODE = 0o700
_PRODUCER_NAME = "chronikwerk"
# Build areas are created by ``tempfile.mkdtemp(prefix="chronikwerk-capture-")``.
_CAPTURE_DIR_RE = re.compile(r"chronikwerk-capture-[A-Za-z0-9_]+")
_ABANDONED_AFTER = timedelta(days=1)
_S3_UNAVAILABLE = (
    "the S3 archive store is not available in this build; use storage.backend: filesystem"
)


@dataclass(frozen=True, slots=True)
class PreservationMaterial:
    """Validated files the preservation pipeline needs: policy, anchors and seal identity."""

    retention: RetentionPolicy
    trust_anchors: tuple[asn1_x509.Certificate, ...]
    seal: SealOptions


@dataclass(frozen=True, slots=True)
class PreservationServices:
    """The application-owned services built for one process."""

    journal: RequestJournal
    catalog: ArchiveCatalog
    scheduler: JournaledSchedulingService


def _issue(path: str, message: str) -> ConfigValidationIssue:
    return ConfigValidationIssue(path=path, message=message)


def _requirement_issue(problem: str) -> ConfigValidationIssue:
    """Key a ``preservation_requirements`` problem by the setting it names first."""
    return _issue(problem.split(" ", 1)[0], problem)


def _run_coroutine[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine to completion from sync code, even when a loop is already running."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


def _seal_options(settings: Settings) -> SealOptions | None:
    seal = settings.seal
    if seal.pfx_path is None:
        return None
    password = seal.pfx_password.get_secret_value() if seal.pfx_password is not None else None
    return SealOptions(pfx_path=seal.pfx_path, pfx_password=password)


def _load_retention(
    settings: Settings, issues: list[ConfigValidationIssue]
) -> RetentionPolicy | None:
    try:
        return load_retention_policy(settings.retention.policy_path)
    except ConfigValidationError as exc:
        issues.extend(
            _issue("retention.policy_path", f"{issue.path}: {issue.message}")
            for issue in exc.issues
        )
        return None


def _load_anchors(
    settings: Settings, issues: list[ConfigValidationIssue]
) -> tuple[asn1_x509.Certificate, ...] | None:
    path = settings.verify.trust_anchors_path
    if path is None:
        return None
    try:
        return load_trust_anchors(path)
    except PermanentError as exc:
        issues.append(_issue("verify.trust_anchors_path", str(exc)))
    except OSError as exc:
        issues.append(_issue("verify.trust_anchors_path", f"cannot read {path}: {exc}"))
    return None


def _check_seal(
    seal: SealOptions | None,
    anchors: tuple[asn1_x509.Certificate, ...] | None,
    issues: list[ConfigValidationIssue],
) -> None:
    if seal is None:
        return
    try:
        problems = _run_coroutine(seal_identity_problems(seal, anchors))
    except OSError as exc:
        problems = [f"cannot read {seal.pfx_path}: {exc}"]
    issues.extend(_issue("seal.pfx_path", problem) for problem in problems)


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        info = os.stat(path)
    except OSError:
        return None
    return info.st_dev, info.st_ino


def _resolves_into(path: Path, root: Path) -> bool:
    """Decide whether ``path`` lies at or below ``root`` by realpath or by device and inode.

    The device/inode comparison of ``path`` and its existing ancestors with ``root`` also
    catches a second mount (bind mount) of the archive root under another name.
    """
    real_path, real_root = Path(os.path.realpath(path)), Path(os.path.realpath(root))
    if real_path == real_root or real_root in real_path.parents:
        return True
    root_identity = _identity(real_root)
    if root_identity is None:
        return False
    return any(
        _identity(candidate) == root_identity for candidate in (real_path, *real_path.parents)
    )


def _writable_directory(path: Path) -> str | None:
    """Return why ``path`` is not an existing, writable directory, or ``None``."""
    if not path.is_dir():
        return f"directory {path} does not exist"
    if not os.access(path, os.W_OK | os.X_OK):
        return f"directory {path} is not writable by the service user"
    return None


def _check_storage_root(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    if settings.storage.backend != "filesystem":
        return
    problem = _writable_directory(settings.storage.root)
    if problem is not None:
        issues.append(_issue("storage.root", problem))


def _check_journal_dir(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    journal_dir = settings.journal.path.parent
    problem = _writable_directory(journal_dir)
    if problem is not None:
        issues.append(_issue("journal.path", problem))
    if _resolves_into(journal_dir, settings.storage.root):
        issues.append(_issue("journal.path", "must be on local disk, not below storage.root"))


def _temp_dir_problem(temp_dir: Path) -> str | None:
    """Return why an existing build area is unsafe, or why a missing one cannot be created."""
    try:
        info = os.lstat(temp_dir)
    except FileNotFoundError:
        if not temp_dir.parent.is_dir():
            return f"directory {temp_dir.parent} does not exist"
        return None
    except OSError as exc:
        return f"cannot inspect {temp_dir}: {exc.strerror or exc}"
    if stat.S_ISLNK(info.st_mode):
        return f"{temp_dir} must not be a symbolic link"
    if not stat.S_ISDIR(info.st_mode):
        return f"{temp_dir} is not a directory"
    if info.st_uid != os.geteuid():
        return f"{temp_dir} must be owned by the service user"
    if stat.S_IMODE(info.st_mode) & 0o077:
        return f"{temp_dir} must not grant group or other access (mode 0700)"
    return None


def _check_temp_dir(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    """Require a local, private build area below no archive root."""
    temp_dir = settings.capture.temp_dir
    if not temp_dir.is_absolute():
        issues.append(_issue("capture.temp_dir", "must be an absolute local path"))
        return
    problem = _temp_dir_problem(temp_dir)
    if problem is not None:
        issues.append(_issue("capture.temp_dir", problem))
    if _resolves_into(temp_dir, settings.storage.root):
        issues.append(_issue("capture.temp_dir", "must be on local disk, not below storage.root"))


def _check_local_paths(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    """Require the archive root, the journal's directory and the build area to be usable."""
    _check_storage_root(settings, issues)
    _check_journal_dir(settings, issues)
    _check_temp_dir(settings, issues)


def _collect(
    settings: Settings,
) -> tuple[list[ConfigValidationIssue], PreservationMaterial | None]:
    issues = [_requirement_issue(problem) for problem in preservation_requirements(settings)]
    if settings.storage.backend == "s3":
        issues.append(_issue("storage.backend", _S3_UNAVAILABLE))
    _check_local_paths(settings, issues)
    retention = _load_retention(settings, issues)
    anchors = _load_anchors(settings, issues)
    seal = _seal_options(settings)
    _check_seal(seal, anchors, issues)
    if issues or retention is None or anchors is None or seal is None:
        return issues, None
    return issues, PreservationMaterial(retention=retention, trust_anchors=anchors, seal=seal)


def startup_problems(settings: Settings) -> list[ConfigValidationIssue]:
    """Return every unmet preservation requirement (D9) for validated settings."""
    issues, _material = _collect(settings)
    return issues


def load_preservation_material(settings: Settings) -> PreservationMaterial:
    """Load the policy, trust anchors and seal identity, or raise listing every problem."""
    issues, material = _collect(settings)
    if issues or material is None:
        raise ConfigValidationError(issues)
    return material


def timestamp_options(settings: Settings) -> TimestampOptions:
    """Translate the RFC 3161 settings and transport hardening for the TSA client."""
    timestamp = settings.signing.timestamp
    rfc3161 = timestamp.rfc3161
    transport = settings.hardening.transport
    return TimestampOptions(
        enabled=timestamp.enabled,
        tsa_url=str(rfc3161.tsa_url) if rfc3161.tsa_url is not None else None,
        timeout_seconds=rfc3161.timeout_seconds,
        ca_bundle_path=rfc3161.ca_bundle_path,
        user=rfc3161.user.get_secret_value() if rfc3161.user is not None else None,
        password=rfc3161.password.get_secret_value() if rfc3161.password is not None else None,
        trust_env=transport.trust_env,
        allow_insecure_http=transport.allow_insecure_http,
        allow_private_networks=transport.allow_private_networks,
    )


def _capture_options(settings: Settings, retention: RetentionPolicy) -> CaptureOptions:
    capture = settings.capture
    fields = settings.fields
    return CaptureOptions(
        consistency_attempts=capture.consistency_attempts,
        max_attachment_bytes=capture.max_attachment_bytes,
        max_package_bytes=capture.max_package_bytes,
        download_timeout_seconds=capture.download_timeout_seconds,
        max_temp_bytes=capture.max_temp_bytes,
        max_metadata_bytes=MAX_METADATA_BYTES,
        record_class_field_name=fields.record_class,
        known_record_classes=frozenset(retention.classes),
        archive_path_field_name=fields.archive_path,
        archive_user_mode_field_name=fields.archive_user_mode,
        archive_user_field_name=fields.archive_user,
    )


def _prepare_temp_dir(path: Path) -> Path:
    """Create the private build area if missing; it must already pass ``startup_problems``."""
    path.mkdir(mode=_TEMP_DIR_MODE, exist_ok=True)
    return path


def _filesystem_store(settings: Settings) -> FilesystemArchiveStore:
    storage = settings.storage
    try:
        return FilesystemArchiveStore(
            storage.root, worm_enforced=storage.filesystem.worm_enforced, fsync=storage.fsync
        )
    except ArchiveStoreError as exc:
        raise ConfigValidationError([_issue("storage.root", str(exc))]) from exc


def abandoned_capture_dirs(temp_dir: Path, *, cutoff: float) -> list[Path]:
    """Return the build areas the sweep may remove (pure guard over ``lstat`` results).

    Only direct children of ``temp_dir`` named like a capture build area that are real
    directories (not symbolic links) last modified before ``cutoff`` qualify.
    """
    try:
        names = sorted(os.listdir(temp_dir))
    except FileNotFoundError:
        return []
    found: list[Path] = []
    for name in names:
        if _CAPTURE_DIR_RE.fullmatch(name) is None:
            continue
        try:
            info = os.lstat(temp_dir / name)
        except OSError:
            continue
        if stat.S_ISDIR(info.st_mode) and info.st_mtime < cutoff:
            found.append(temp_dir / name)
    return found


def sweep_capture_temp(
    temp_dir: Path,
    *,
    now: datetime,
    older_than: timedelta = _ABANDONED_AFTER,
    remove: Callable[..., None] = remove_tree_within_root,
) -> int:
    """Remove abandoned capture build areas, confined to ``temp_dir``; return how many."""
    removed = 0
    for path in abandoned_capture_dirs(temp_dir, cutoff=(now - older_than).timestamp()):
        remove(path, storage_root=temp_dir, missing_ok=True, fsync=False)
        removed += 1
    return removed


def _housekeeping(store: FilesystemArchiveStore, temp_dir: Path) -> Callable[[], Awaitable[None]]:
    """Return the maintenance callback that removes abandoned staging and build areas."""

    async def run() -> None:
        now = now_utc()
        try:
            staged = await asyncio.to_thread(
                store.cleanup_abandoned_staging, older_than=_ABANDONED_AFTER, now=now
            )
            if staged:
                log.info("housekeeping.staging_removed", count=staged)
        finally:
            swept = await asyncio.to_thread(sweep_capture_temp, temp_dir, now=now)
            if swept:
                log.info("housekeeping.capture_temp_removed", count=swept)

    return run


def preservation_options(
    settings: Settings,
    material: PreservationMaterial,
    *,
    workflow: ArchiveWorkflowOptions,
    documents: DocumentOptions,
) -> PreservationOptions:
    """Translate validated settings and loaded material into pipeline options."""
    return PreservationOptions(
        workflow=workflow,
        documents=documents,
        capture=_capture_options(settings, material.retention),
        seal=material.seal,
        trust_anchors=material.trust_anchors,
        reading_copies=tuple(settings.pdf.reading_copies),
        retention_rules=material.retention.classes,
        temp_dir=settings.capture.temp_dir,
        producer=Producer(_PRODUCER_NAME, VERSION),
        source_origin=str(settings.zammad.base_url),
    )


def build_preservation_services(
    settings: Settings,
    material: PreservationMaterial,
    *,
    client: AsyncZammadClient,
    workflow: ArchiveWorkflowOptions,
    documents: DocumentOptions,
) -> PreservationServices:
    """Open the journal and catalog, then wire the store, pipeline and scheduler around them."""
    store = _filesystem_store(settings)
    options = preservation_options(settings, material, workflow=workflow, documents=documents)
    timestamper = build_timestamper(timestamp_options(settings))
    _prepare_temp_dir(settings.capture.temp_dir)
    retry = settings.capture.retry
    journal = RequestJournal(
        JournalOptions(
            path=settings.journal.path,
            retry_max_attempts=retry.max_attempts,
            retry_max_delay_seconds=retry.max_delay_seconds,
        )
    )
    try:
        catalog = ArchiveCatalog(settings.journal.path)
    except BaseException:
        journal.close()
        raise
    pipeline = ArchivePipeline(
        options,
        client=client,
        journal=journal,
        catalog=catalog,
        store=store,
        timestamper=timestamper,
    )
    admission = settings.admission
    scheduler = JournaledSchedulingService(
        journal=journal,
        processor=pipeline,
        max_pending=admission.max_pending,
        max_running=admission.max_running,
        shutdown_timeout_seconds=admission.shutdown_timeout_seconds,
        housekeeping=_housekeeping(store, settings.capture.temp_dir),
    )
    return PreservationServices(journal=journal, catalog=catalog, scheduler=scheduler)
