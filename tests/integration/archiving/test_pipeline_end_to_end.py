"""Run the public ticket processor against a fake Zammad, real PDFs, and a real filesystem."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from chronikwerk.archiving.options import ArchiveRuntimeOptions, ArchiveWorkflowOptions
from chronikwerk.archiving.processor import build_ticket_processor
from chronikwerk.archiving.workflow import ArchiveOutcome
from chronikwerk.documents.options import DocumentOptions, SigningOptions, TimestampOptions
from chronikwerk.operations.guards import TicketGuards
from chronikwerk.operations.history import JobHistory
from chronikwerk.operations.job import TicketJob
from chronikwerk.storage.options import ArchiveStorageOptions
from chronikwerk.zammad.dto import Ticket
from tests.support.fake_zammad import FakeZammad, default_ticket
from tests.support.zammad_fixtures import load_zammad_fixture

_PROCESSING_PHASE = [
    "remove_tag:pdf:error",
    "remove_tag:pdf:sign",
    "add_tag:pdf:processing",
]
_DONE_PHASE = [
    "remove_tag:pdf:processing",
    "remove_tag:pdf:error",
    "remove_tag:pdf:sign",
    "add_tag:pdf:signed",
]


def _options(root: Path, *, acknowledge: bool = True) -> ArchiveRuntimeOptions:
    """Build unsigned runtime options for a temporary archive root."""
    return ArchiveRuntimeOptions(
        workflow=ArchiveWorkflowOptions(
            trigger_tag="pdf:sign",
            require_trigger_tag=True,
            acknowledge_on_success=acknowledge,
            archive_path_field_name="archive_path",
            archive_user_mode_field_name="archive_user_mode",
            archive_user_field_name="archive_user",
        ),
        documents=DocumentOptions(
            max_articles=250,
            article_limit_mode="fail",
            locale="en-GB",
            timezone="UTC",
            signing=SigningOptions(
                enabled=False,
                pfx_path=None,
                pfx_password=None,
                reason="r",
                location="l",
                timestamp=TimestampOptions(
                    enabled=False,
                    tsa_url=None,
                    timeout_seconds=1.0,
                    ca_bundle_path=None,
                    user=None,
                    password=None,
                    trust_env=False,
                    allow_insecure_http=False,
                    allow_private_networks=False,
                ),
            ),
        ),
        storage=ArchiveStorageOptions(
            root=root,
            fsync=False,
            filename_pattern="Ticket-{ticket_number}_{timestamp_utc}.pdf",
        ),
    )


def _run(
    fake: FakeZammad,
    root: Path,
    *deliveries: str | None,
    ttl: int = 0,
    history: JobHistory | None = None,
    **options: object,
) -> list[ArchiveOutcome]:
    """Process one payload per delivery id through the public processor."""
    process = build_ticket_processor(
        _options(root, **options),  # type: ignore[arg-type]
        client=fake.client,
        guards=TicketGuards(delivery_id_ttl_seconds=ttl),
        history=history if history is not None else JobHistory(),
    )

    async def scenario() -> list[ArchiveOutcome]:
        return [
            await process(TicketJob(ticket_id=1, payload={"ticket_id": 1}, delivery_id=delivery))
            for delivery in deliveries
        ]

    return asyncio.run(scenario())


def _artifacts(root: Path) -> tuple[list[Path], list[Path]]:
    """List PDFs and sidecars below the archive root."""
    return sorted(root.rglob("*.pdf")), sorted(root.rglob("*.pdf.json"))


def test_successful_run_publishes_before_done_tags_and_posts_note(tmp_path: Path) -> None:
    fake = FakeZammad(tags=["pdf:sign"])
    seen_at_done: list[tuple[int, int]] = []

    def on_add(tag: str) -> None:
        if tag == "pdf:signed":
            pdfs, sidecars = _artifacts(tmp_path)
            seen_at_done.append((len(pdfs), len(sidecars)))

    fake.on_add_tag = on_add

    (outcome,) = _run(fake, tmp_path, None)

    assert outcome.status == "processed"
    assert fake.names == [
        "get_ticket",
        "list_tags",
        *_PROCESSING_PHASE,
        "list_articles",
        *_DONE_PHASE,
        "create_internal_article",
    ]
    assert seen_at_done == [(1, 1)]
    assert fake.tags == {"pdf:signed"}
    assert len(fake.notes) == 1


def test_successful_run_writes_pdf_and_sidecar_under_owner_and_path(tmp_path: Path) -> None:
    fake = FakeZammad(tags=["pdf:sign"])

    _run(fake, tmp_path, None)

    (pdf,), (sidecar,) = _artifacts(tmp_path)
    assert pdf.parent == tmp_path / "alice" / "Customers" / "ACME"
    assert pdf.name.startswith("Ticket-10001_")
    assert pdf.read_bytes().startswith(b"%PDF-")
    record = json.loads(sidecar.read_text(encoding="utf-8"))
    assert record["ticket_id"] == 1
    assert record["ticket_number"] == "10001"
    assert record["signing"] == {"enabled": False, "tsa_used": False}
    assert record["storage_path"] == str(pdf)


def test_documented_zammad_ticket_shape_archives_under_owner_and_path(tmp_path: Path) -> None:
    ticket = Ticket.model_validate(load_zammad_fixture("ticket_show_expand.json"))
    fake = FakeZammad(tags=["pdf:sign"], ticket=ticket)

    (outcome,) = _run(fake, tmp_path, None)

    assert outcome.status == "processed"
    (pdf,), _sidecars = _artifacts(tmp_path)
    assert pdf.parent == tmp_path / "alice" / "Customers" / "ACME"
    assert pdf.name.startswith("Ticket-31042_")


def test_retry_in_current_agent_mode_refuses_the_service_account(tmp_path: Path) -> None:
    ticket = Ticket.model_validate(
        {
            **default_ticket().model_dump(exclude={"owner", "updated_by", "customer"}),
            "updated_by": "chronikwerk-api",
            "archive_user_mode": "current_agent",
        }
    )
    fake = FakeZammad(tags=["pdf:sign"], ticket=ticket)
    fake.current_login = "chronikwerk-api"

    (outcome,) = _run(fake, tmp_path, None)

    assert outcome.status == "failed_permanent"
    assert _artifacts(tmp_path) == ([], [])
    assert "get_current_user_login" in fake.names
    assert fake.tags == {"pdf:error"}
    (_subject, body), = fake.notes
    assert "service_account_as_agent" in body
    assert "missing_user_login" not in body


def test_no_success_note_when_acknowledgement_is_disabled(tmp_path: Path) -> None:
    fake = FakeZammad(tags=["pdf:sign"])

    (outcome,) = _run(fake, tmp_path, None, acknowledge=False)

    assert outcome.status == "processed"
    assert "create_internal_article" not in fake.names


def test_failing_success_note_still_reports_processed(tmp_path: Path) -> None:
    fake = FakeZammad(tags=["pdf:sign"])
    fake.fail_note = True
    history = JobHistory()

    (outcome,) = _run(fake, tmp_path, None, history=history)

    assert outcome.status == "processed"
    assert fake.tags == {"pdf:signed"}
    assert [e["status"] for e in history.read(10)][0] == "processed"
    assert "processed_with_warning" in {e["status"] for e in history.read(10)}


def test_ticket_without_trigger_tag_is_skipped_without_mutation(tmp_path: Path) -> None:
    fake = FakeZammad(tags=["other"])

    (outcome,) = _run(fake, tmp_path, None)

    assert outcome.status == "skipped_not_triggered"
    assert fake.names == ["get_ticket", "list_tags"]
    assert fake.tags == {"other"}
    assert _artifacts(tmp_path) == ([], [])


def test_same_delivery_id_is_processed_once(tmp_path: Path) -> None:
    fake = FakeZammad(tags=["pdf:sign"])

    first, second = _run(fake, tmp_path, "delivery-e2e", "delivery-e2e", ttl=60)

    assert first.status == "processed"
    assert second.status == "skipped_idempotency"
    assert fake.names.count("get_ticket") == 1


def test_failed_done_transition_keeps_artifacts_and_marks_error(tmp_path: Path) -> None:
    fake = FakeZammad(tags=["pdf:sign"])

    def on_add(tag: str) -> None:
        if tag == "pdf:signed":
            raise RuntimeError("zammad tag endpoint down")

    fake.on_add_tag = on_add

    (outcome,) = _run(fake, tmp_path, None)

    assert outcome.status == "failed_permanent"
    assert fake.names.count("add_tag:pdf:signed") == 4
    pdfs, sidecars = _artifacts(tmp_path)
    assert (len(pdfs), len(sidecars)) == (1, 1)
    assert fake.tags == {"pdf:error"}
