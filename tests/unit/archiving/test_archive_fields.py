"""Carry configured archive fields into the PDF under the names its template reads."""

from __future__ import annotations

from chronikwerk.archiving.location import snapshot_custom_fields
from chronikwerk.archiving.options import ArchiveWorkflowOptions
from chronikwerk.documents.models import Snapshot, TicketMeta
from chronikwerk.documents.templates import render_html
from chronikwerk.zammad.dto import Ticket


def _workflow() -> ArchiveWorkflowOptions:
    """Configure renamed Zammad field names, as `fields.*` allows."""
    return ArchiveWorkflowOptions(
        trigger_tag="pdf:sign",
        require_trigger_tag=True,
        acknowledge_on_success=True,
        archive_path_field_name="chronik_path",
        archive_user_mode_field_name="chronik_mode",
        archive_user_field_name="chronik_user",
    )


def _ticket() -> Ticket:
    """Return an expanded ticket that sets the renamed attributes."""
    return Ticket.model_validate(
        {
            "id": 42,
            "number": "31042",
            "owner": "alice",
            "chronik_path": "Customers > ACME",
            "chronik_mode": "current_agent",
            "archive_path": "Unrelated > Field",
        }
    )


def test_renamed_fields_reach_the_snapshot_under_their_canonical_names() -> None:
    assert snapshot_custom_fields(_ticket(), _workflow()) == {
        "archive_path": "Customers > ACME",
        "archive_user_mode": "current_agent",
    }


def test_pdf_header_shows_the_renamed_archive_path_and_mode() -> None:
    snapshot = Snapshot(
        ticket=TicketMeta(
            id=42,
            number="31042",
            custom_fields=snapshot_custom_fields(_ticket(), _workflow()),
        ),
        articles=[],
    )

    html = render_html(snapshot, locale="en-GB", timezone="UTC")

    assert "Customers &gt; ACME" in html
    assert "current_agent" in html
    assert "Unrelated" not in html
