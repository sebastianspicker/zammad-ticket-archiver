"""Report a complete revision back to Zammad: done tags, success note and journal status."""

# Contract: preservation brief (ADRs 0011-0013) sections 5.6 (step 7) and 5.10.
# A failure here never fails the request: the revision is complete and stays in the store;
# ``zammad_status=failed`` lets reconciliation retry the update (acceptance check A12).

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import structlog

from chronikwerk.archiving.notes import (
    RevisionNotePayload,
    revision_note_html,
    revision_note_subject,
)
from chronikwerk.archiving.options import ArchiveWorkflowOptions
from chronikwerk.archiving.retry import async_retry
from chronikwerk.archiving.tags import apply_done
from chronikwerk.concurrency import run_sync_cancellation_safe
from chronikwerk.operations.journal import JournalRequest, RequestJournal, ZammadStatus
from chronikwerk.preservation.catalog import CatalogRevision
from chronikwerk.preservation.manifest import MANIFEST_NAME, Coverage, parse_manifest
from chronikwerk.storage.archive_store import ArchiveStore
from chronikwerk.timestamps import format_timestamp_utc
from chronikwerk.zammad.errors import NotFoundError
from chronikwerk.zammad.gateway import AsyncZammadClient

__all__ = ["coverage_summary", "publish_revision_status"]

log = structlog.get_logger(__name__)

# Same retry as the legacy workflow's terminal tag update.
TAG_RETRIES = 3
TAG_BACKOFF_BASE_SECONDS = 0.5
TAG_BACKOFF_FACTOR = 2.0
_COVERAGE_UNAVAILABLE = "unavailable"


def coverage_summary(coverage: Coverage) -> str:
    """Render the manifest coverage as one short line."""
    emails = coverage.original_emails
    return (
        f"articles {coverage.articles.included}/{coverage.articles.total}, "
        f"attachments {coverage.attachments.included}/{coverage.attachments.total}, "
        f"original emails {emails.included}/{emails.expected} "
        f"({len(emails.unavailable)} unavailable), "
        f"{'complete' if coverage.complete else 'incomplete'}"
    )


def _stored_coverage(store: ArchiveStore, revision: CatalogRevision) -> str:
    """Read the coverage from the stored manifest; the catalog does not hold it."""
    try:
        with store.open(f"{revision.key_prefix}{MANIFEST_NAME}") as handle:
            manifest = parse_manifest(handle.read())
    except Exception:  # pylint: disable=broad-exception-caught
        log.warning(
            "archive_pipeline.coverage_unreadable",
            revision_id=revision.revision_id,
            key_prefix=revision.key_prefix,
        )
        return _COVERAGE_UNAVAILABLE
    return coverage_summary(manifest.coverage)


def _note_payload(
    request: JournalRequest, revision: CatalogRevision, *, coverage: str, now: datetime
) -> RevisionNotePayload:
    retain_until = revision.retain_until
    return RevisionNotePayload(
        revision_number=revision.revision_number,
        revision_id=revision.revision_id,
        manifest_sha256=revision.manifest_sha256,
        key_prefix=revision.key_prefix,
        coverage=coverage,
        retain_until=format_timestamp_utc(retain_until) if retain_until else "none",
        retention_enforced=revision.retention_enforced,
        request_id=request.request_id,
        delivery_id=request.delivery_id,
        timestamp_utc=format_timestamp_utc(now),
    )


async def _update_zammad(
    *,
    client: AsyncZammadClient,
    store: ArchiveStore,
    workflow: ArchiveWorkflowOptions,
    request: JournalRequest,
    revision: CatalogRevision,
    now: datetime,
) -> None:
    await async_retry(
        lambda: apply_done(client, request.ticket_id),
        max_retries=TAG_RETRIES,
        backoff_base=TAG_BACKOFF_BASE_SECONDS,
        backoff_factor=TAG_BACKOFF_FACTOR,
    )
    if workflow.acknowledge_on_success:
        coverage = await run_sync_cancellation_safe(_stored_coverage, store, revision)
        payload = _note_payload(request, revision, coverage=coverage, now=now)
        await client.create_internal_article(
            request.ticket_id, revision_note_subject(), revision_note_html(payload)
        )


async def publish_revision_status(
    *,
    client: AsyncZammadClient,
    journal: RequestJournal,
    store: ArchiveStore,
    workflow: ArchiveWorkflowOptions,
    request: JournalRequest,
    revision: CatalogRevision,
    clock: Callable[[], datetime],
) -> None:
    """Apply ``pdf:signed`` and post the success note for a COMPLETE request.

    Success sets ``zammad_status=done``. A ticket Zammad no longer knows (404) also ends as
    ``done``: nothing is left to update. Any other failure is logged and sets ``failed``; a
    row that is already ``failed`` is not written again, so it keeps the time of its first
    failure and the journal purge ends its retries. A cancellation leaves ``pending`` for
    reconciliation.
    """
    try:
        await _update_zammad(
            client=client,
            store=store,
            workflow=workflow,
            request=request,
            revision=revision,
            now=clock(),
        )
    except NotFoundError:
        log.warning(
            "archive_pipeline.zammad_ticket_gone",
            ticket_id=request.ticket_id,
            request_id=request.request_id,
            revision_id=revision.revision_id,
        )
        await run_sync_cancellation_safe(
            journal.set_zammad_status, request.request_id, ZammadStatus.DONE
        )
        return
    except Exception:  # pylint: disable=broad-exception-caught
        log.exception(
            "archive_pipeline.zammad_status_failed",
            ticket_id=request.ticket_id,
            request_id=request.request_id,
            revision_id=revision.revision_id,
            key_prefix=revision.key_prefix,
        )
        if request.zammad_status is not ZammadStatus.FAILED:
            await run_sync_cancellation_safe(
                journal.set_zammad_status, request.request_id, ZammadStatus.FAILED
            )
        return
    await run_sync_cancellation_safe(
        journal.set_zammad_status, request.request_id, ZammadStatus.DONE
    )
    log.info(
        "archive_pipeline.done",
        ticket_id=request.ticket_id,
        request_id=request.request_id,
        revision_id=revision.revision_id,
        revision_number=revision.revision_number,
    )
