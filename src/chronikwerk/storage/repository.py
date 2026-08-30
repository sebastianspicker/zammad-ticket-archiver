"""Write PDFs and audit sidecars through the archive repository.

All writes go through a temp directory first; files are renamed into place atomically.
The sidecar JSON is moved last so its presence reliably signals a complete archival.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from chronikwerk.storage.audit import AuditRecordInput, build_audit_record
from chronikwerk.storage.filesystem import (
    move_file_within_root,
    path_entry_exists,
    remove_tree_within_root,
    unlink_file_within_root,
    write_bytes,
)
from chronikwerk.storage.options import ArchiveStorageOptions, SigningProvenance

_UNSIGNED_PROVENANCE = SigningProvenance(False, False)

if TYPE_CHECKING:
    from chronikwerk.documents.models import Snapshot


@dataclass(frozen=True)
class StorageResult:
    """Describe the durable storage result returned to the pipeline."""

    target_path: Path
    sidecar_path: Path
    sha256_hex: str
    size_bytes: int


@dataclass(frozen=True)
class StoreTicketFilesRequest:
    """Collect all inputs required to persist one archived ticket."""

    pdf_bytes: bytes
    snapshot: Snapshot
    target_path: Path
    sidecar_path: Path
    ticket_id: int
    now: datetime
    storage: ArchiveStorageOptions
    signing_provenance: SigningProvenance
    signing_cert_fingerprint: str | None = None


@dataclass(frozen=True)
class AuditWriteContext:
    """Collect non-secret values needed for the archive audit sidecar."""

    ticket_id: int
    snapshot: Snapshot
    now: datetime
    target_path: Path
    sha256_hex: str
    storage: ArchiveStorageOptions
    signing_provenance: SigningProvenance


@dataclass(frozen=True)
class RollbackFailure:
    """A rollback operation that could not restore transactional state."""

    operation: str
    path: Path
    error: Exception


@dataclass(slots=True)
class _StorageTransaction:
    """Track canonical files, backups, and commit state for one archive write."""

    target_path: Path
    sidecar_path: Path
    transaction_id: str
    storage_root: Path
    fsync: bool
    pdf_backup: Path | None = None
    sidecar_backup: Path | None = None
    pdf_committed: bool = False
    sidecar_committed: bool = False

    @property
    def backup_paths(self) -> tuple[Path | None, Path | None]:
        """Return backups in cleanup order: sidecar first, then PDF."""
        return self.sidecar_backup, self.pdf_backup


class StorageTransactionError(Exception):
    """A commit error whose rollback also failed.

    ``recovery_paths`` contains transaction backups left on disk for manual
    recovery. The original commit failure remains available as ``primary_error``.
    """

    def __init__(
        self,
        primary_error: Exception,
        rollback_failures: tuple[RollbackFailure, ...],
        recovery_paths: tuple[Path, ...],
    ) -> None:
        self.primary_error = primary_error
        self.rollback_failures = rollback_failures
        self.recovery_paths = recovery_paths
        failures = ", ".join(
            f"{failure.operation}: {failure.path}" for failure in rollback_failures
        )
        recovery = ", ".join(str(path) for path in recovery_paths) or "none"
        super().__init__(
            "Storage transaction failed and rollback was incomplete "
            f"(primary error: {primary_error}; rollback failures: {failures}; "
            f"recoverable backups: {recovery})"
        )


def _build_and_write_audit(
    tmp_dir: Path,
    sidecar_name: str,
    *,
    context: AuditWriteContext,
) -> None:
    """Build the audit record and write the sidecar JSON into *tmp_dir*."""
    audit_record = build_audit_record(
        AuditRecordInput(
            ticket_id=context.ticket_id,
            ticket_number=context.snapshot.ticket.number,
            title=context.snapshot.ticket.title,
            created_at=context.now,
            storage_path=str(context.target_path),
            sha256=context.sha256_hex,
            articles_total=(
                context.snapshot.articles_total
                if context.snapshot.articles_total is not None
                else len(context.snapshot.articles) + context.snapshot.articles_omitted
            ),
            articles_included=len(context.snapshot.articles),
            articles_omitted=context.snapshot.articles_omitted,
        ),
        signing_provenance=context.signing_provenance,
    )
    audit_bytes = (
        json.dumps(audit_record, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    write_bytes(
        tmp_dir / sidecar_name,
        audit_bytes,
        fsync=context.storage.fsync,
        storage_root=context.storage.root,
    )


def _backup_if_exists(
    path: Path,
    *,
    transaction_id: str,
    storage_root: Path,
    fsync: bool,
) -> Path | None:
    """Move an existing canonical file to a collision-proof transaction backup."""
    backup_path = path.with_name(f"{path.name}.bak.{transaction_id}")
    try:
        move_file_within_root(path, backup_path, storage_root=storage_root, fsync=fsync)
    except FileNotFoundError:
        return None
    return backup_path


def _log_cleanup_failure(operation: str, path: Path, exc: BaseException) -> None:
    structlog.get_logger(__name__).error(
        "ticket_storage.cleanup_failed",
        operation=operation,
        path=str(path),
        error=str(exc),
    )


def _remove_for_rollback(
    path: Path,
    *,
    storage_root: Path,
    fsync: bool,
) -> RollbackFailure | None:
    try:
        unlink_file_within_root(
            path,
            storage_root=storage_root,
            missing_ok=True,
            fsync=fsync,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _log_cleanup_failure("rollback_remove", path, exc)
        return RollbackFailure("rollback_remove", path, exc)
    return None


def _restore_backup(
    backup_path: Path | None,
    canonical_path: Path,
    *,
    storage_root: Path,
    fsync: bool,
) -> RollbackFailure | None:
    if backup_path is None:
        return None
    try:
        move_file_within_root(
            backup_path,
            canonical_path,
            storage_root=storage_root,
            fsync=fsync,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _log_cleanup_failure("rollback_restore", backup_path, exc)
        return RollbackFailure("rollback_restore", backup_path, exc)
    return None


def _cleanup_backup(path: Path | None, *, storage_root: Path, fsync: bool) -> None:
    if path is None:
        return
    try:
        unlink_file_within_root(
            path,
            storage_root=storage_root,
            missing_ok=True,
            fsync=fsync,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _log_cleanup_failure("backup_remove", path, exc)


def _append_rollback_failure(
    failures: list[RollbackFailure],
    failure: RollbackFailure | None,
) -> None:
    if failure is not None:
        failures.append(failure)


def _rollback_committed_files(
    transaction: _StorageTransaction,
) -> list[RollbackFailure]:
    failures: list[RollbackFailure] = []
    if transaction.sidecar_committed:
        _append_rollback_failure(
            failures,
            _remove_for_rollback(
                transaction.sidecar_path,
                storage_root=transaction.storage_root,
                fsync=transaction.fsync,
            ),
        )
    if transaction.pdf_committed:
        _append_rollback_failure(
            failures,
            _remove_for_rollback(
                transaction.target_path,
                storage_root=transaction.storage_root,
                fsync=transaction.fsync,
            ),
        )
    _append_rollback_failure(
        failures,
        _restore_backup(
            transaction.sidecar_backup,
            transaction.sidecar_path,
            storage_root=transaction.storage_root,
            fsync=transaction.fsync,
        ),
    )
    _append_rollback_failure(
        failures,
        _restore_backup(
            transaction.pdf_backup,
            transaction.target_path,
            storage_root=transaction.storage_root,
            fsync=transaction.fsync,
        ),
    )
    return failures


def _inspect_recovery_paths(
    transaction: _StorageTransaction,
    *,
    failures: list[RollbackFailure],
) -> tuple[Path, ...]:
    recovery_paths: list[Path] = []
    for backup_path in transaction.backup_paths:
        if backup_path is None:
            continue
        try:
            if path_entry_exists(backup_path, storage_root=transaction.storage_root):
                recovery_paths.append(backup_path)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _log_cleanup_failure("recovery_inspect", backup_path, exc)
            failures.append(RollbackFailure("recovery_inspect", backup_path, exc))
    return tuple(recovery_paths)


def _commit_files_to_storage(
    tmp_dir: Path,
    transaction: _StorageTransaction,
) -> None:
    """Atomically rename files from *tmp_dir* into their final locations.

    Order matters: the PDF is committed first, then the sidecar last.
    The sidecar arriving last signals a complete, successful archival.
    """
    try:
        transaction.pdf_backup = _backup_if_exists(
            transaction.target_path,
            transaction_id=transaction.transaction_id,
            storage_root=transaction.storage_root,
            fsync=transaction.fsync,
        )
        transaction.sidecar_backup = _backup_if_exists(
            transaction.sidecar_path,
            transaction_id=transaction.transaction_id,
            storage_root=transaction.storage_root,
            fsync=transaction.fsync,
        )
        move_file_within_root(
            tmp_dir / transaction.target_path.name,
            transaction.target_path,
            storage_root=transaction.storage_root,
            fsync=transaction.fsync,
        )
        transaction.pdf_committed = True
        move_file_within_root(
            tmp_dir / transaction.sidecar_path.name,
            transaction.sidecar_path,
            storage_root=transaction.storage_root,
            fsync=transaction.fsync,
        )
        transaction.sidecar_committed = True
    except Exception as primary_error:
        _raise_rollback_error(primary_error, transaction)
        raise
    _cleanup_backups(transaction)


def _raise_rollback_error(
    primary_error: Exception,
    transaction: _StorageTransaction,
) -> None:
    rollback_failures = _rollback_committed_files(transaction)
    if rollback_failures:
        recovery_paths = _inspect_recovery_paths(
            transaction,
            failures=rollback_failures,
        )
        raise StorageTransactionError(
            primary_error, tuple(rollback_failures), recovery_paths
        ) from primary_error


def _cleanup_backups(
    transaction: _StorageTransaction,
) -> None:
    for backup_path in transaction.backup_paths:
        _cleanup_backup(
            backup_path,
            storage_root=transaction.storage_root,
            fsync=transaction.fsync,
        )


def store_ticket_files_request(request: StoreTicketFilesRequest) -> StorageResult:
    """Write a PDF and audit sidecar from the application storage request.

    Uses a temp directory under the target parent; all files are renamed into place.
    The sidecar is moved last so its presence reliably indicates a complete archival.
    """
    transaction_id = uuid.uuid4().hex
    temp_archive_root = request.target_path.parent / (
        f".tmp-archiving-{request.ticket_id}-{transaction_id}"
    )
    sha256_hex = sha256(request.pdf_bytes).hexdigest()
    transaction = _StorageTransaction(
        target_path=request.target_path,
        sidecar_path=request.sidecar_path,
        transaction_id=transaction_id,
        storage_root=request.storage.root,
        fsync=request.storage.fsync,
    )

    try:
        _write_staged_files(
            temp_archive_root,
            request.pdf_bytes,
            request.sidecar_path.name,
            context=AuditWriteContext(
                ticket_id=request.ticket_id,
                snapshot=request.snapshot,
                now=request.now,
                target_path=request.target_path,
                sha256_hex=sha256_hex,
                storage=request.storage,
                signing_provenance=request.signing_provenance,
            ),
        )
        _commit_files_to_storage(
            temp_archive_root,
            transaction,
        )
    finally:
        _cleanup_temp_archive(temp_archive_root, request.storage)

    return StorageResult(
        target_path=request.target_path,
        sidecar_path=request.sidecar_path,
        sha256_hex=sha256_hex,
        size_bytes=len(request.pdf_bytes),
    )


def _write_staged_files(
    temp_archive_root: Path,
    pdf_bytes: bytes,
    sidecar_name: str,
    *,
    context: AuditWriteContext,
) -> None:
    write_bytes(
        temp_archive_root / context.target_path.name,
        pdf_bytes,
        fsync=context.storage.fsync,
        storage_root=context.storage.root,
    )
    _build_and_write_audit(temp_archive_root, sidecar_name, context=context)


def _cleanup_temp_archive(temp_archive_root: Path, storage: ArchiveStorageOptions) -> None:
    try:
        remove_tree_within_root(
            temp_archive_root,
            storage_root=storage.root,
            missing_ok=True,
            fsync=storage.fsync,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _log_cleanup_failure("temp_remove", temp_archive_root, exc)
