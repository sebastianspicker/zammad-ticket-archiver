"""Process one journaled archive request end to end, or resume its interrupted commit.

Contract: preservation brief (ADRs 0011-0013) sections 5.3-5.6, 5.10 and 5.11.
``ArchivePipeline`` satisfies the scheduler's ``RequestProcessor`` protocol: ``process`` takes
a COLLECTING request, ``resume`` a VERIFYING one after a restart. Each call leaves the row
COMPLETE, FAILED or WAITING (transient back-off), except on cancellation: then the row stays
COLLECTING or VERIFYING for restart recovery.

Order of one ``process`` call:

1. Eligibility: the trigger tag (or ``pdf:processing`` from an interrupted attempt) is set,
   or the request is a forced retry or mail evidence. Otherwise the request fails with
   ``not_triggered`` without touching the ticket.
2. ``pdf:processing``, capture into a temporary build area, policy ID.
3. A complete revision with the same source digest and policy completes the request
   directly (acceptance check A04).
4. Otherwise: reading copies from the captured articles, package assembly, sealed commit.
5. The temporary build area is removed; journal completion and ``pdf:signed`` with the
   success note follow outside it. A failure in the Zammad update only marks
   ``zammad_status=failed``.

Failures before completion follow the note policy of ``pipeline_failures`` and are journaled
as transient (back-off) or permanent with their error code. A failure after the revision was
published (commit step ``retention_set`` or ``cataloged``) is always transient and keeps the
revision: the next ``process`` call resumes that commit instead of capturing again. When the
pipeline posted the final failure note itself, the row's Zammad status becomes DONE; otherwise
it stays PENDING and the scheduler's maintenance calls ``report_failure`` later.
``publish_pending`` retries the Zammad update of a COMPLETE row.

An exception raised after the request was settled (such as a failing build-area cleanup) is
logged only: the complete revision is still reported. Store, catalog, journal and build-area
I/O runs off the event loop.
"""

from __future__ import annotations

import tempfile
from collections.abc import Awaitable, Callable, Collection
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from pyhanko.sign.timestamps.api import TimeStamper

from chronikwerk.archiving.assemble import AssembledPackage, assemble_package
from chronikwerk.archiving.capture import CapturedSource, capture_ticket
from chronikwerk.archiving.commit import CommitContext, CommitStep, commit_revision
from chronikwerk.archiving.commit_checks import seal_signer_fingerprint
from chronikwerk.archiving.commit_errors import CommitInterruptedError
from chronikwerk.archiving.commit_resume import resume_revision
from chronikwerk.archiving.location import (
    archive_custom_fields,
    resolve_archive_location,
    snapshot_custom_fields,
)
from chronikwerk.archiving.path import archive_user_mode
from chronikwerk.archiving.pipeline_failures import (
    FailureOutcome,
    final_failure_note,
    journal_failure,
    journaled_failure_note,
    retry_failure_note,
)
from chronikwerk.archiving.pipeline_options import (
    PreservationOptions,
    RetentionRule,
    compute_retain_until,
    rule_document,
)
from chronikwerk.archiving.pipeline_status import publish_revision_status
from chronikwerk.archiving.rendering import ReadingCopies, render_reading_copies
from chronikwerk.archiving.tags import PROCESSING_TAG, apply_processing
from chronikwerk.archiving.workflow_errors import (
    ClassifiedFailure,
    FailureTarget,
    classify_failure,
    post_failure_note,
    project_final_failure,
)
from chronikwerk.concurrency import run_sync_cancellation_safe
from chronikwerk.operations.journal import (
    JournalRequest,
    RequestJournal,
    RequestSource,
    RequestState,
    ZammadStatus,
)
from chronikwerk.operations.metrics import failed_total, processed_total, skipped_total
from chronikwerk.preservation.catalog import ArchiveCatalog, CatalogRevision
from chronikwerk.preservation.digest import policy_id
from chronikwerk.preservation.revision import new_revision_id, revision_key_prefix
from chronikwerk.storage.archive_store import ArchiveStore
from chronikwerk.timestamps import now_utc
from chronikwerk.zammad.dto import TagList, Ticket
from chronikwerk.zammad.gateway import AsyncZammadClient

__all__ = ["ArchivePipeline", "PreservationOptions"]

log = structlog.get_logger(__name__)

NOT_TRIGGERED = "not_triggered"
_CURRENT_AGENT_MODE = "current_agent"
_WORK_DIR_PREFIX = "chronikwerk-capture-"
_IN_FLIGHT = frozenset({RequestState.COLLECTING, RequestState.VERIFYING})
# Commit steps after which the revision is published in the store and must not be redone.
_PUBLISHED_STEPS = frozenset({CommitStep.RETENTION_SET, CommitStep.CATALOGED})
# Sources that archive without the trigger tag: an explicit retry and attached mail evidence.
_UNTRIGGERED_SOURCES = frozenset({RequestSource.RETRY, RequestSource.MAIL_EVIDENCE})


class ArchivePipeline:
    """Capture, package, seal and commit journaled requests; report the outcome to Zammad."""

    def __init__(
        self,
        options: PreservationOptions,
        *,
        client: AsyncZammadClient,
        journal: RequestJournal,
        catalog: ArchiveCatalog,
        store: ArchiveStore,
        timestamper: TimeStamper,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        """Bind the immutable options and the application-owned services."""
        self._options = options
        self._client = client
        self._journal = journal
        self._catalog = catalog
        self._store = store
        self._timestamper = timestamper
        self._clock = clock
        self._capture_options = options.capture_options()
        self._signer_sha256: str | None = None

    async def process(self, request: JournalRequest) -> None:
        """Run one COLLECTING request to COMPLETE, FAILED or WAITING (transient back-off).

        A row that comes back from a failure after its revision was published resumes that
        commit: no new capture, no new revision.
        """
        with structlog.contextvars.bound_contextvars(
            ticket_id=request.ticket_id, request_id=request.request_id
        ):
            if _published(request):
                log.info("archive_pipeline.resume_published", commit_step=request.commit_step)
                await self._settle(request, self._resume_published)
                return
            try:
                tags = await self._client.list_tags(request.ticket_id)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # Eligibility is unknown, so the ticket is not touched.
                await self._fail(request, exc, project_to_ticket=False)
                return
            if not self._eligible(request, tags.root):
                await self._skip_not_triggered(request)
                return
            await self._settle(request, lambda current: self._archive(current, tags))

    async def resume(self, request: JournalRequest) -> None:
        """Finish the interrupted commit of a VERIFYING request from what the store holds.

        A missing or unreadable commit context, or a commit that never stored its seal,
        fails the request transiently with ``commit_interrupted`` so it is captured again.
        """
        with structlog.contextvars.bound_contextvars(
            ticket_id=request.ticket_id, request_id=request.request_id
        ):
            await self._settle(request, self._resume_commit)

    async def publish_pending(self, request: JournalRequest) -> None:
        """Retry ``pdf:signed`` and the success note of a COMPLETE row; never changes its state."""
        with structlog.contextvars.bound_contextvars(
            ticket_id=request.ticket_id, request_id=request.request_id
        ):
            revision = await self._completed_revision(request)
            if revision is None:
                log.warning("archive_pipeline.publish_without_revision", state=request.state)
                return
            await self._publish(request, revision)

    async def report_failure(self, request: JournalRequest) -> None:
        """Post the final note and ``pdf:error`` for a FAILED row, then mark Zammad DONE.

        When Zammad is unreachable the status stays PENDING for the next maintenance run.
        """
        with structlog.contextvars.bound_contextvars(
            ticket_id=request.ticket_id, request_id=request.request_id
        ):
            trigger_tag = self._options.workflow.trigger_tag
            reported = await project_final_failure(
                client=self._client,
                target=_target(request),
                note=journaled_failure_note(
                    request,
                    retry_max_attempts=self._journal.retry_max_attempts,
                    trigger_tag=trigger_tag,
                ),
                trigger_tag=trigger_tag,
            )
            if not reported:
                log.warning("archive_pipeline.failure_report_pending", code=request.error_code)
                return
            await run_sync_cancellation_safe(
                self._journal.set_zammad_status, request.request_id, ZammadStatus.DONE
            )

    async def _settle(
        self,
        request: JournalRequest,
        run: Callable[[JournalRequest], Awaitable[CatalogRevision]],
    ) -> None:
        """Run the commit work, journal a failure, and report a complete revision to Zammad."""
        try:
            revision = await run(request)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            settled = await self._fail(request, exc, project_to_ticket=True)
            if settled is None:
                return
            revision = settled
        processed_total.inc()
        await self._publish(request, revision)

    def _eligible(self, request: JournalRequest, tags: Collection[str]) -> bool:
        workflow = self._options.workflow
        return (
            request.source in _UNTRIGGERED_SOURCES
            or not workflow.require_trigger_tag
            or workflow.trigger_tag in tags
            # An attempt interrupted by a restart already replaced the trigger tag.
            or PROCESSING_TAG in tags
        )

    async def _skip_not_triggered(self, request: JournalRequest) -> None:
        trigger_tag = self._options.workflow.trigger_tag
        log.info("archive_pipeline.skip_not_triggered", trigger_tag=trigger_tag)
        skipped_total.labels(reason=NOT_TRIGGERED).inc()
        await run_sync_cancellation_safe(self._journal_not_triggered, request.request_id)

    def _journal_not_triggered(self, request_id: str) -> None:
        trigger_tag = self._options.workflow.trigger_tag
        self._journal.fail_permanent(
            request_id,
            error_code=NOT_TRIGGERED,
            error_detail=f"ticket does not carry the trigger tag {trigger_tag}",
        )
        self._journal.set_zammad_status(request_id, ZammadStatus.DONE)

    def _new_work_dir(self) -> tempfile.TemporaryDirectory[str]:
        return tempfile.TemporaryDirectory(dir=self._options.temp_dir, prefix=_WORK_DIR_PREFIX)

    async def _archive(self, request: JournalRequest, tags: TagList) -> CatalogRevision:
        """Capture the ticket and return the complete revision that holds it.

        The build area is removed before an unchanged capture completes the request.
        """
        await apply_processing(
            self._client,
            request.ticket_id,
            trigger_tag=self._options.workflow.trigger_tag,
            force_reprocess=request.source is RequestSource.RETRY,
        )
        work_dir = await run_sync_cancellation_safe(self._new_work_dir)
        try:
            revision, unchanged = await self._capture_and_commit(request, tags, Path(work_dir.name))
        finally:
            await run_sync_cancellation_safe(work_dir.cleanup)
        if unchanged:
            log.info("archive_pipeline.unchanged", revision_id=revision.revision_id)
            await run_sync_cancellation_safe(
                self._journal.complete, request.request_id, revision_id=revision.revision_id
            )
        return revision

    async def _capture_and_commit(
        self, request: JournalRequest, tags: TagList, work_dir: Path
    ) -> tuple[CatalogRevision, bool]:
        """Return the revision holding the capture and whether it already existed."""
        captured = await capture_ticket(
            self._client,
            ticket_id=request.ticket_id,
            work_dir=work_dir,
            options=self._capture_options,
            clock=self._clock,
        )
        rule = self._options.retention_rules[captured.record_class]
        policy = policy_id(
            record_class_rule=rule_document(rule),
            reading_copies=self._options.reading_copies,
        )
        existing = await run_sync_cancellation_safe(
            self._catalog.find_matching,
            ticket_id=request.ticket_id,
            source_digest=captured.source_digest,
            policy_id=policy,
        )
        if existing is not None:
            return existing, True
        return await self._commit_new_revision(request, captured, tags, rule, policy), False

    async def _commit_new_revision(
        self,
        request: JournalRequest,
        captured: CapturedSource,
        tags: TagList,
        rule: RetentionRule,
        policy: str,
    ) -> CatalogRevision:
        location = await self._archive_location(captured.ticket, request.trigger_login)
        copies = await render_reading_copies(
            client=self._client,
            ticket_id=request.ticket_id,
            ticket=captured.ticket,
            tags=tags,
            custom_fields=snapshot_custom_fields(captured.ticket, self._options.workflow),
            options=self._options.documents,
            variants=self._options.variants,
            articles=captured.articles,
        )
        assembled, ctx = await run_sync_cancellation_safe(
            self._assemble, request, captured, copies, rule, policy, location
        )
        return await commit_revision(
            assembled,
            ctx,
            store=self._store,
            catalog=self._catalog,
            journal=self._journal,
            seal_options=self._options.seal,
            timestamper=self._timestamper,
            trust_anchors=self._options.trust_anchors,
            clock=self._clock,
            expected_signer_sha256=await self._seal_signer_sha256(),
        )

    async def _seal_signer_sha256(self) -> str:
        """Return the seal certificate fingerprint, read from the PFX once per pipeline."""
        if self._signer_sha256 is None:
            self._signer_sha256 = await run_sync_cancellation_safe(
                seal_signer_fingerprint, self._options.seal
            )
        return self._signer_sha256

    async def _archive_location(
        self, ticket: Ticket, trigger_login: str | None
    ) -> tuple[str, tuple[str, ...]]:
        """Resolve the sanitized archive user and path.

        ``current_agent`` names the agent whose ticket update triggered the request; only a
        request without that login (retry, reconciliation) falls back to the fetched ticket.
        """
        workflow = self._options.workflow
        mode = archive_user_mode(
            archive_custom_fields(ticket, workflow), workflow.archive_user_mode_field_name
        )
        service_login = (
            await self._client.get_current_user_login() if mode == _CURRENT_AGENT_MODE else None
        )
        payload: dict[str, Any] = (
            {"ticket": {"updated_by": {"login": trigger_login}}} if trigger_login else {}
        )
        return resolve_archive_location(
            workflow, ticket=ticket, payload=payload, service_login=service_login
        )

    def _assemble(
        self,
        request: JournalRequest,
        captured: CapturedSource,
        copies: ReadingCopies,
        rule: RetentionRule,
        policy: str,
        location: tuple[str, tuple[str, ...]],
    ) -> tuple[AssembledPackage, CommitContext]:
        """Allocate the revision, compute its retention and write the manifest (blocking)."""
        ticket = captured.ticket
        revision_number = self._catalog.next_revision_number(ticket.id)
        revision_id = new_revision_id()
        previous = self._catalog.latest_for_ticket(ticket.id)
        # The manifest records whole seconds; retention starts from the recorded time.
        captured_at = captured.collection_finished.replace(microsecond=0)
        retain_until = compute_retain_until(
            rule, captured_at=captured_at, ticket_raw=captured.ticket_raw
        )
        archive_user, archive_path = location
        ctx = CommitContext(
            request_id=request.request_id,
            ticket_id=ticket.id,
            ticket_number=ticket.number,
            key_prefix=revision_key_prefix(
                record_class=captured.record_class,
                archive_user=archive_user,
                archive_path=archive_path,
                ticket_number=ticket.number,
                revision_number=revision_number,
                revision_id=revision_id,
            ),
            revision_id=revision_id,
            revision_number=revision_number,
            record_class=captured.record_class,
            archive_user=archive_user,
            archive_path=archive_path,
            retain_until=retain_until,
            collection_started=captured.collection_started,
        )
        assembled = assemble_package(
            captured,
            copies,
            revision_id=revision_id,
            revision_number=revision_number,
            previous_revision_id=None if previous is None else previous.revision_id,
            policy_id=policy,
            captured_at=captured_at,
            producer=self._options.producer,
            capture_description=self._capture_description(request, rule, retain_until),
        )
        return assembled, ctx

    def _capture_description(
        self, request: JournalRequest, rule: RetentionRule, retain_until: datetime
    ) -> dict[str, Any]:
        """Return the capture facts only the pipeline knows (brief section 5.4)."""
        limits = self._capture_options
        return {
            "source": {"system": "zammad", "origin": self._options.source_origin},
            "request": {
                "id": request.request_id,
                "source": request.source.value,
                "delivery_id": request.delivery_id,
                "received_at": request.received_at,
                "attempt": request.attempts + 1,
            },
            "retention": {
                "rule": rule_document(rule),
                "retain_until": retain_until,
                "enforced": self._store.retention_enforced,
            },
            "limits": {
                "consistency_attempts": limits.consistency_attempts,
                "max_attachment_bytes": limits.max_attachment_bytes,
                "max_package_bytes": limits.max_package_bytes,
                "max_temp_bytes": limits.max_temp_bytes,
                "max_metadata_bytes": limits.max_metadata_bytes,
                "download_timeout_seconds": limits.download_timeout_seconds,
            },
        }

    async def _resume_commit(self, request: JournalRequest) -> CatalogRevision:
        return await self._resume_from(request, _commit_context(request))

    async def _resume_published(self, request: JournalRequest) -> CatalogRevision:
        """Resume the published commit of a leased COLLECTING row (it becomes VERIFYING)."""
        ctx = _commit_context(request)
        verifying = await run_sync_cancellation_safe(self._mark_verifying_again, request, ctx)
        return await self._resume_from(verifying, ctx)

    def _mark_verifying_again(self, request: JournalRequest, ctx: CommitContext) -> JournalRequest:
        """Return the row to VERIFYING; its kept commit step and context stay (blocking)."""
        self._journal.mark_verifying(
            request.request_id,
            revision_id=ctx.revision_id,
            revision_number=ctx.revision_number,
            context_json=request.context_json,
        )
        current = self._journal.get(request.request_id)
        if current is None:
            raise CommitInterruptedError("the request disappeared from the journal")
        return current

    async def _resume_from(self, request: JournalRequest, ctx: CommitContext) -> CatalogRevision:
        revision = await resume_revision(
            request,
            ctx,
            store=self._store,
            catalog=self._catalog,
            journal=self._journal,
            seal_options=self._options.seal,
            trust_anchors=self._options.trust_anchors,
            clock=self._clock,
            expected_signer_sha256=await self._seal_signer_sha256(),
        )
        if revision is None:
            raise CommitInterruptedError("the commit stopped before the seal files were stored")
        return revision

    async def _publish(self, request: JournalRequest, revision: CatalogRevision) -> None:
        await publish_revision_status(
            client=self._client,
            journal=self._journal,
            store=self._store,
            workflow=self._options.workflow,
            request=request,
            revision=revision,
            clock=self._clock,
        )

    async def _fail(
        self, request: JournalRequest, exc: Exception, *, project_to_ticket: bool
    ) -> CatalogRevision | None:
        """Report a failure before completion to Zammad, then journal it with its code.

        The full exception goes to the log; note and journal get the sanitized message. A
        request that is no longer COLLECTING or VERIFYING was settled before ``exc`` was
        raised: nothing is projected or journaled, and its complete revision (if any) is
        returned so the caller still reports it.
        """
        current = await self._current_row(request)
        if current is not None and current.state not in _IN_FLIGHT:
            log.warning(
                "archive_pipeline.error_after_settlement", state=current.state.value, exc_info=exc
            )
            return await self._completed_revision(current)
        failure = classify_failure(exc, trigger_tag=self._options.workflow.trigger_tag)
        # A resume that found nothing to finish captures again, like any interrupted commit.
        published = _published(current) and not isinstance(exc, CommitInterruptedError)
        transient = failure.transient or published
        exhausted = transient and request.attempts + 1 > self._journal.retry_max_attempts
        failed_total.inc()
        log.error(
            "archive_pipeline.failed",
            code=failure.code,
            transient=transient,
            exhausted=exhausted,
            revision_kept=published,
            exc_info=exc,
        )
        final = not transient or exhausted
        reported = project_to_ticket and await self._project(
            request, failure, final=final, exhausted=exhausted
        )
        await run_sync_cancellation_safe(
            journal_failure,
            self._journal,
            request.request_id,
            failure,
            FailureOutcome(transient=transient, keep_revision=published, reported=reported),
        )
        return None

    async def _project(
        self, request: JournalRequest, failure: ClassifiedFailure, *, final: bool, exhausted: bool
    ) -> bool:
        """Apply the note policy; return whether a final note and ``pdf:error`` reached Zammad."""
        trigger_tag = self._options.workflow.trigger_tag
        if final:
            attempts = request.attempts + 1 if exhausted else None
            note = final_failure_note(failure, attempts=attempts, trigger_tag=trigger_tag)
            return await project_final_failure(
                client=self._client, target=_target(request), note=note, trigger_tag=trigger_tag
            )
        if request.attempts == 0:
            await post_failure_note(
                client=self._client, target=_target(request), note=retry_failure_note(failure)
            )
        else:
            log.info("archive_pipeline.retrying_silently", attempt=request.attempts + 1)
        return False

    async def _current_row(self, request: JournalRequest) -> JournalRequest | None:
        """Return the current journal row; ``None`` when it is unknown or unreadable."""
        try:
            return await run_sync_cancellation_safe(self._journal.get, request.request_id)
        except Exception:  # pylint: disable=broad-exception-caught
            log.warning("archive_pipeline.journal_unreadable", exc_info=True)
            return None

    async def _completed_revision(self, row: JournalRequest) -> CatalogRevision | None:
        if row.state is not RequestState.COMPLETE or row.revision_id is None:
            return None
        return await run_sync_cancellation_safe(self._catalog.get, row.revision_id)


def _target(request: JournalRequest) -> FailureTarget:
    return FailureTarget(request.ticket_id, request.request_id, request.delivery_id)


def _published(row: JournalRequest | None) -> bool:
    """Return whether the row's revision was already published in the store."""
    return row is not None and row.context_json is not None and row.commit_step in _PUBLISHED_STEPS


def _commit_context(request: JournalRequest) -> CommitContext:
    if request.context_json is None:
        raise CommitInterruptedError("no commit context was journaled for the request")
    try:
        return CommitContext.from_json(request.context_json)
    except ValueError as exc:
        raise CommitInterruptedError(f"journaled commit context is unusable: {exc}") from exc
