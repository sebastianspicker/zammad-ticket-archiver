from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from zammad_pdf_archiver.domain.snapshot_models import (
    Article,
    AttachmentMeta,
    PartyRef,
    Snapshot,
    TicketMeta,
)


def test_default_template_renders_example_snapshot() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    template_dir = repo_root / "templates" / "default"

    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("ticket.html")

    snapshot = Snapshot(
        ticket=TicketMeta(
            id=1,
            number="T1",
            title="Printer-friendly rendering",
            created_at=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            updated_at=datetime(2024, 1, 2, 12, 30, tzinfo=UTC),
            customer=PartyRef(name="Acme Corp", email="support@acme.invalid"),
            owner=PartyRef(login="agent1", name="Agent One"),
            tags=["pdf:sign", "billing"],
            custom_fields={
                "archive_path": ["ACME", "2024", "Invoices"],
                "archive_user_mode": "owner",
            },
        ),
        articles=[
            Article(
                id=100,
                created_at=datetime(2024, 1, 1, 10, 5, tzinfo=UTC),
                internal=False,
                sender="customer@acme.invalid",
                subject="Initial request",
                body_html="<p>Hello <strong>World</strong></p>",
                body_text="Hello World",
                attachments=[
                    AttachmentMeta(
                        article_id=100,
                        attachment_id=10,
                        filename="invoice.pdf",
                        size=12345,
                        content_type="application/pdf",
                    )
                ],
            )
        ],
    )

    html = template.render(snapshot=snapshot, ticket=snapshot.ticket, articles=snapshot.articles)

    assert "Ticket T1" in html
    assert "Hello" in html
