"""Resume a commit that a restart interrupted while its request was VERIFYING.

The local build directory does not survive a restart, so a resume works only from what the
store holds. When the published prefix or the staged revision holds ``manifest.json``, its
seal and its token, everything is read back against the stored manifest, the seal and token
are checked again, and the revision is published, cataloged and completed. Otherwise the
staged revision is aborted and ``resume_revision`` returns ``None``: the caller then fails the
request transiently with ``CommitInterruptedError.code`` and a new capture gets a new
revision ID.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from asn1crypto import x509 as asn1_x509

from chronikwerk.archiving.commit import (
    CommitContext,
    abort_quietly,
    begin_revision,
    catalog_revision,
    complete_cataloged,
    finish_revision,
    signer_fingerprint,
)
from chronikwerk.archiving.commit_checks import (
    SEAL_FILES,
    check_seal_and_token,
    read_back,
    read_object,
    store_errors,
)
from chronikwerk.archiving.commit_errors import (
    SealCheckFailedError,
    StoreConflictError,
    StoreWriteFailedError,
    TimestampCheckFailedError,
    VerificationFailedError,
)
from chronikwerk.concurrency import run_sync_cancellation_safe
from chronikwerk.operations.journal import JournalRequest, RequestJournal, RequestState
from chronikwerk.preservation.catalog import ArchiveCatalog, CatalogRevision
from chronikwerk.preservation.manifest import (
    MANIFEST_NAME,
    SEAL_NAME,
    TOKEN_NAME,
    Manifest,
    ManifestError,
    parse_manifest,
)
from chronikwerk.preservation.seal import SealOptions
from chronikwerk.storage.archive_store import ArchiveStore, RevisionWriter
from chronikwerk.timestamps import now_utc

__all__ = ["resume_revision"]

_Anchors = tuple[asn1_x509.Certificate, ...] | list[asn1_x509.Certificate]
_ABORTING_ERRORS = (
    SealCheckFailedError,
    StoreConflictError,
    StoreWriteFailedError,
    TimestampCheckFailedError,
    VerificationFailedError,
)


def _check_request(request: JournalRequest, ctx: CommitContext) -> None:
    """Refuse a context that does not describe the journaled VERIFYING request."""
    if request.state is not RequestState.VERIFYING:
        raise ValueError(f"request {request.request_id} is {request.state.value}, not verifying")
    if (request.request_id, request.revision_id, request.revision_number) != (
        ctx.request_id,
        ctx.revision_id,
        ctx.revision_number,
    ):
        raise ValueError(f"commit context does not match request {request.request_id}")


def _stored_manifest(writer: RevisionWriter, ctx: CommitContext) -> tuple[bytes, Manifest]:
    """Read and parse the stored manifest; it must describe the revision being resumed."""
    data = read_object(writer, MANIFEST_NAME)
    try:
        manifest = parse_manifest(data)
    except ManifestError as exc:
        raise VerificationFailedError(f"stored manifest is invalid: {exc}") from exc
    stored = (manifest.revision_id, manifest.revision_number, manifest.ticket.id)
    if stored != (ctx.revision_id, ctx.revision_number, ctx.ticket_id):
        raise VerificationFailedError("stored manifest describes a different revision")
    if manifest.record_class != ctx.record_class:
        raise VerificationFailedError("stored manifest has a different record class")
    return data, manifest


def _stored_seal_files(
    writer: RevisionWriter, ctx: CommitContext
) -> tuple[bytes, Manifest, bytes, bytes]:
    """Return the stored manifest bytes, the parsed manifest, the seal and the token."""
    manifest_bytes, manifest = _stored_manifest(writer, ctx)
    return manifest_bytes, manifest, read_object(writer, SEAL_NAME), read_object(writer, TOKEN_NAME)


def _stored_keys(writer: RevisionWriter) -> set[str]:
    with store_errors("list stored objects"):
        return set(writer.keys())


async def _reverify_and_finish(
    writer: RevisionWriter,
    ctx: CommitContext,
    *,
    store: ArchiveStore,
    catalog: ArchiveCatalog,
    journal: RequestJournal,
    seal_options: SealOptions,
    trust_anchors: _Anchors,
    clock: Callable[[], datetime],
    expected_signer_sha256: str | None,
) -> CatalogRevision:
    manifest_bytes, manifest, seal, token = await run_sync_cancellation_safe(
        _stored_seal_files, writer, ctx
    )
    await check_seal_and_token(
        manifest_bytes,
        seal,
        token,
        trust_anchors=trust_anchors,
        expected_signer_sha256=await signer_fingerprint(seal_options, expected_signer_sha256),
        window=(ctx.collection_started, clock()),
    )
    await run_sync_cancellation_safe(
        read_back,
        writer,
        manifest,
        {MANIFEST_NAME: manifest_bytes, SEAL_NAME: seal, TOKEN_NAME: token},
    )
    revision = catalog_revision(
        ctx,
        manifest,
        manifest_bytes,
        retention_enforced=store.retention_enforced,
        verified_at=clock(),
    )
    return await finish_revision(writer, ctx, revision, catalog=catalog, journal=journal)


async def resume_revision(
    request: JournalRequest,
    ctx: CommitContext,
    *,
    store: ArchiveStore,
    catalog: ArchiveCatalog,
    journal: RequestJournal,
    seal_options: SealOptions,
    trust_anchors: _Anchors,
    clock: Callable[[], datetime] = now_utc,
    expected_signer_sha256: str | None = None,
) -> CatalogRevision | None:
    """Finish an interrupted commit from the store, or abort it and return ``None``.

    ``ctx`` must describe the journaled request (same request, revision ID and number); the
    journal does not record the key prefix, so the caller supplies it. Store, catalog and
    journal calls run off the event loop; ``expected_signer_sha256`` is as in
    ``commit_revision``.
    """
    _check_request(request, ctx)
    existing = await run_sync_cancellation_safe(catalog.get, ctx.revision_id)
    if existing is not None:
        return await run_sync_cancellation_safe(
            complete_cataloged, existing, ctx, catalog=catalog, journal=journal
        )
    writer = await run_sync_cancellation_safe(begin_revision, store, ctx.key_prefix, resume=True)
    try:
        keys = await run_sync_cancellation_safe(_stored_keys, writer)
        if not set(SEAL_FILES) <= keys:
            await run_sync_cancellation_safe(abort_quietly, writer)
            return None
        return await _reverify_and_finish(
            writer,
            ctx,
            store=store,
            catalog=catalog,
            journal=journal,
            seal_options=seal_options,
            trust_anchors=trust_anchors,
            clock=clock,
            expected_signer_sha256=expected_signer_sha256,
        )
    except _ABORTING_ERRORS:
        await run_sync_cancellation_safe(abort_quietly, writer)
        raise
