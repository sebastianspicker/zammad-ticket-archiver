"""Seal, timestamp, store, read back, publish and catalog one assembled revision.

Contract: preservation brief (ADRs 0011-0013) sections 5.5 and 5.6 (commit steps).
Every finished step is recorded with ``RequestJournal.record_commit_step``; a restart resumes
through ``chronikwerk.archiving.commit_resume.resume_revision``.

The catalog and the journal use separate SQLite connections, so the final step cannot be one
transaction: the catalog row is written first, then the request is completed. A resume that
finds the catalog row treats the revision as cataloged and only completes the request.

Failures are raised as typed errors with a ``code`` (``commit_errors``); recording them in
the journal (``fail_transient``/``fail_permanent``) is the caller's job.

Every store, journal, catalog and build-directory operation runs off the event loop through
``run_sync_cancellation_safe``, one call per commit step, so a cancelled caller never detaches
a half-finished step.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from asn1crypto import x509 as asn1_x509
from pyhanko.sign.timestamps.api import TimeStamper

from chronikwerk.archiving.assemble import AssembledPackage
from chronikwerk.archiving.commit_checks import (
    check_seal_and_token,
    read_back,
    seal_signer_fingerprint,
    store_errors,
)
from chronikwerk.archiving.commit_errors import (
    SealCheckFailedError,
    StoreConflictError,
    StoreWriteFailedError,
    TimestampCheckFailedError,
    TimestampUnavailableError,
    VerificationFailedError,
)
from chronikwerk.concurrency import run_sync_cancellation_safe
from chronikwerk.failures import PermanentError, TransientError
from chronikwerk.operations.journal import RequestJournal
from chronikwerk.preservation.catalog import ArchiveCatalog, CatalogRevision
from chronikwerk.preservation.manifest import MANIFEST_NAME, SEAL_NAME, TOKEN_NAME, Manifest
from chronikwerk.preservation.seal import SealOptions, seal_manifest, timestamp_seal
from chronikwerk.storage.archive_store import ArchiveStore, RevisionWriter
from chronikwerk.timestamps import now_utc

__all__ = [
    "CommitContext",
    "CommitStep",
    "abort_quietly",
    "begin_revision",
    "catalog_revision",
    "commit_revision",
    "complete_cataloged",
    "finish_revision",
    "signer_fingerprint",
]

_Anchors = tuple[asn1_x509.Certificate, ...] | list[asn1_x509.Certificate]
_ABORTING_ERRORS = (StoreWriteFailedError, StoreConflictError, VerificationFailedError)
_VERIFICATION_RESULT = "passed"
_CONTEXT_VERSION = 1


class CommitStep(StrEnum):
    """Commit steps as recorded in ``requests.commit_step``."""

    SEALED = "sealed"
    CONTENT_UPLOADED = "content_uploaded"
    SEAL_UPLOADED = "seal_uploaded"
    READ_BACK = "read_back"
    RETENTION_SET = "retention_set"
    CATALOGED = "cataloged"


@dataclass(frozen=True, slots=True)
class CommitContext:
    """Identity, location and retention of the revision being committed."""

    request_id: str
    ticket_id: int
    ticket_number: str
    key_prefix: str
    revision_id: str
    revision_number: int
    record_class: str
    archive_user: str
    archive_path: tuple[str, ...]
    retain_until: datetime
    collection_started: datetime

    def to_json(self) -> str:
        """Serialize the context as stable, versioned JSON (datetimes in UTC ISO 8601)."""
        document = {
            "version": _CONTEXT_VERSION,
            "request_id": self.request_id,
            "ticket_id": self.ticket_id,
            "ticket_number": self.ticket_number,
            "key_prefix": self.key_prefix,
            "revision_id": self.revision_id,
            "revision_number": self.revision_number,
            "record_class": self.record_class,
            "archive_user": self.archive_user,
            "archive_path": list(self.archive_path),
            "retain_until": _format_context_time(self.retain_until),
            "collection_started": _format_context_time(self.collection_started),
        }
        return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> Self:
        """Parse ``to_json`` output; raise ``ValueError`` for anything else."""
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"commit context is not valid JSON: {exc}") from exc
        if not isinstance(document, dict) or document.get("version") != _CONTEXT_VERSION:
            raise ValueError(f"commit context must be a version {_CONTEXT_VERSION} JSON object")
        archive_path = document.get("archive_path")
        if not isinstance(archive_path, list) or not all(
            isinstance(segment, str) for segment in archive_path
        ):
            raise ValueError("commit context archive_path must be a list of strings")
        return cls(
            request_id=_context_value(document, "request_id", str),
            ticket_id=_context_value(document, "ticket_id", int),
            ticket_number=_context_value(document, "ticket_number", str),
            key_prefix=_context_value(document, "key_prefix", str),
            revision_id=_context_value(document, "revision_id", str),
            revision_number=_context_value(document, "revision_number", int),
            record_class=_context_value(document, "record_class", str),
            archive_user=_context_value(document, "archive_user", str),
            archive_path=tuple(archive_path),
            retain_until=_parse_context_time(document, "retain_until"),
            collection_started=_parse_context_time(document, "collection_started"),
        )


def _format_context_time(value: datetime) -> str:
    """Render an aware datetime as UTC ISO 8601 with a ``Z`` suffix."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("commit context datetimes must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _context_value[T](document: Mapping[str, Any], key: str, kind: type[T]) -> T:
    """Return ``document[key]`` when it has type ``kind`` (booleans are never integers)."""
    value = document.get(key)
    if not isinstance(value, kind) or isinstance(value, bool):
        raise ValueError(f"commit context {key} must be a {kind.__name__}")
    return value


def _parse_context_time(document: Mapping[str, Any], key: str) -> datetime:
    """Parse one aware ISO 8601 datetime of the context."""
    value = datetime.fromisoformat(_context_value(document, key, str))
    if value.tzinfo is None:
        raise ValueError(f"commit context {key} must carry a time zone")
    return value.astimezone(UTC)


async def _seal_locally(
    assembled: AssembledPackage, seal_options: SealOptions, timestamper: TimeStamper
) -> tuple[bytes, bytes]:
    """Seal and timestamp the manifest and write both files into the build directory."""
    try:
        seal = await seal_manifest(assembled.manifest_bytes, seal_options)
    except (PermanentError, TransientError) as exc:
        raise SealCheckFailedError(f"manifest could not be sealed: {exc}") from exc
    try:
        token = await timestamp_seal(seal, timestamper)
    except TransientError as exc:
        raise TimestampUnavailableError(f"TSA unavailable: {exc}") from exc
    except PermanentError as exc:
        raise TimestampCheckFailedError(f"TSA refused the request: {exc}") from exc
    return seal, token


def _keep_seal_files(
    assembled: AssembledPackage,
    ctx: CommitContext,
    journal: RequestJournal,
    seal_files: tuple[bytes, bytes],
) -> None:
    """Write seal and token into the build directory, then record the ``sealed`` step."""
    for name, data in zip((SEAL_NAME, TOKEN_NAME), seal_files, strict=True):
        with (assembled.build_dir / name).open("xb") as handle:
            handle.write(data)
    journal.record_commit_step(ctx.request_id, CommitStep.SEALED)


def abort_quietly(writer: RevisionWriter) -> None:
    """Discard an unpublished revision; a failing abort leaves it for staging cleanup."""
    with suppress(Exception):
        writer.abort()


def _upload_step(
    writer: RevisionWriter,
    assembled: AssembledPackage,
    ctx: CommitContext,
    journal: RequestJournal,
    step: tuple[tuple[str, ...], CommitStep],
) -> None:
    """Write the named build-directory files, then record the finished step."""
    names, finished = step
    for name in names:
        with store_errors(f"store {name}"):
            writer.put_new(name, assembled.build_dir / name, retain_until=ctx.retain_until)
    journal.record_commit_step(ctx.request_id, finished)


async def _upload(
    writer: RevisionWriter,
    assembled: AssembledPackage,
    ctx: CommitContext,
    journal: RequestJournal,
) -> None:
    """Write the listed files, then manifest, seal and token, recording each step."""
    content = tuple(file.path for file in assembled.manifest.files)
    for step in (
        (content, CommitStep.CONTENT_UPLOADED),
        ((MANIFEST_NAME, SEAL_NAME, TOKEN_NAME), CommitStep.SEAL_UPLOADED),
    ):
        await run_sync_cancellation_safe(_upload_step, writer, assembled, ctx, journal, step)


def catalog_revision(
    ctx: CommitContext,
    manifest: Manifest,
    manifest_bytes: bytes,
    *,
    retention_enforced: bool,
    verified_at: datetime,
) -> CatalogRevision:
    """Return the catalog row for a verified, published revision."""
    return CatalogRevision(
        revision_id=ctx.revision_id,
        ticket_id=ctx.ticket_id,
        ticket_number=ctx.ticket_number,
        revision_number=ctx.revision_number,
        record_class=ctx.record_class,
        archive_user=ctx.archive_user,
        archive_path=ctx.archive_path,
        key_prefix=ctx.key_prefix,
        captured_at=manifest.captured_at,
        source_digest=manifest.source_digest,
        policy_id=manifest.policy_id,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        retain_until=ctx.retain_until,
        retention_enforced=retention_enforced,
        verified_at=verified_at,
        verification_result=_VERIFICATION_RESULT,
    )


def complete_cataloged(
    revision: CatalogRevision,
    ctx: CommitContext,
    *,
    catalog: ArchiveCatalog,
    journal: RequestJournal,
) -> CatalogRevision:
    """Insert the catalog row unless it exists, then complete the request (catalog first)."""
    existing = catalog.get(revision.revision_id)
    if existing is None:
        catalog.add_revision(revision)
        existing = revision
    journal.record_commit_step(ctx.request_id, CommitStep.CATALOGED)
    journal.complete(ctx.request_id, revision_id=ctx.revision_id)
    return existing


def _publish_final(writer: RevisionWriter, ctx: CommitContext, journal: RequestJournal) -> None:
    """Publish the staged revision with its final retention, then record the step."""
    with store_errors(f"publish {ctx.key_prefix}"):
        writer.finalize(retain_until=ctx.retain_until)
    journal.record_commit_step(ctx.request_id, CommitStep.RETENTION_SET)


async def finish_revision(
    writer: RevisionWriter,
    ctx: CommitContext,
    revision: CatalogRevision,
    *,
    catalog: ArchiveCatalog,
    journal: RequestJournal,
) -> CatalogRevision:
    """Run the steps after a successful read-back: publish with final retention, catalog."""
    await run_sync_cancellation_safe(
        journal.record_commit_step, ctx.request_id, CommitStep.READ_BACK
    )
    await run_sync_cancellation_safe(_publish_final, writer, ctx, journal)
    return await run_sync_cancellation_safe(
        complete_cataloged, revision, ctx, catalog=catalog, journal=journal
    )


async def signer_fingerprint(options: SealOptions, expected: str | None) -> str:
    """Return ``expected`` or, when unset, read the seal certificate fingerprint off-loop."""
    if expected is not None:
        return expected
    return await run_sync_cancellation_safe(seal_signer_fingerprint, options)


async def commit_revision(
    assembled: AssembledPackage,
    ctx: CommitContext,
    *,
    store: ArchiveStore,
    catalog: ArchiveCatalog,
    journal: RequestJournal,
    seal_options: SealOptions,
    timestamper: TimeStamper,
    trust_anchors: _Anchors,
    clock: Callable[[], datetime] = now_utc,
    expected_signer_sha256: str | None = None,
) -> CatalogRevision:
    """Commit an assembled package for a COLLECTING request and return its catalog row.

    The request moves to VERIFYING with ``ctx`` serialized into the journal for a resume.
    Nothing reaches the store before the seal and token pass their checks. A store or
    read-back failure aborts the unpublished revision before the error is raised.
    ``expected_signer_sha256`` is the caller's cached seal certificate fingerprint; when
    unset it is read from the PFX.
    """
    await run_sync_cancellation_safe(
        journal.mark_verifying,
        ctx.request_id,
        revision_id=ctx.revision_id,
        revision_number=ctx.revision_number,
        context_json=ctx.to_json(),
    )
    seal, token = await _seal_locally(assembled, seal_options, timestamper)
    await run_sync_cancellation_safe(_keep_seal_files, assembled, ctx, journal, (seal, token))
    await check_seal_and_token(
        assembled.manifest_bytes,
        seal,
        token,
        trust_anchors=trust_anchors,
        expected_signer_sha256=await signer_fingerprint(seal_options, expected_signer_sha256),
        window=(ctx.collection_started, clock()),
    )
    writer = await run_sync_cancellation_safe(begin_revision, store, ctx.key_prefix)
    try:
        await _upload(writer, assembled, ctx, journal)
        await run_sync_cancellation_safe(
            read_back,
            writer,
            assembled.manifest,
            {MANIFEST_NAME: assembled.manifest_bytes, SEAL_NAME: seal, TOKEN_NAME: token},
        )
        revision = catalog_revision(
            ctx,
            assembled.manifest,
            assembled.manifest_bytes,
            retention_enforced=store.retention_enforced,
            verified_at=clock(),
        )
        return await finish_revision(writer, ctx, revision, catalog=catalog, journal=journal)
    except _ABORTING_ERRORS:
        await run_sync_cancellation_safe(abort_quietly, writer)
        raise


def begin_revision(store: ArchiveStore, key_prefix: str, *, resume: bool = False) -> RevisionWriter:
    """Open (or, with ``resume``, reopen) a staged revision; store failures are commit errors."""
    action = "resume revision" if resume else "begin revision"
    with store_errors(f"{action} {key_prefix}"):
        return store.begin_revision(key_prefix, resume=resume)
