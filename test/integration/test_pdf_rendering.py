from __future__ import annotations

import logging
import warnings

from zammad_pdf_archiver.adapters.pdf.render_pdf import render_pdf
from zammad_pdf_archiver.domain.snapshot_models import Snapshot


def test_render_pdf_default_template_produces_pdf_bytes() -> None:
    snapshot_dict = {
        "ticket": {
            "id": 1,
            "number": "T1",
            "title": "PDF rendering integration test",
            "created_at": "2024-01-01T10:00:00Z",
            "updated_at": "2024-01-02T12:30:00Z",
            "customer": {"name": "Acme Corp", "email": "support@acme.invalid"},
            "owner": {"login": "agent1", "name": "Agent One"},
            "tags": ["pdf:sign", "billing"],
            "custom_fields": {
                "archive_path": ["ACME", "2024", "Invoices"],
                "archive_user_mode": "owner",
            },
        },
        "articles": [
            {
                "id": 100,
                "created_at": "2024-01-01T10:05:00Z",
                "internal": False,
                "sender": "customer@acme.invalid",
                "subject": "Initial request",
                "body_html": "<p>Hello <strong>World</strong></p>",
                "body_text": "Hello World",
                "attachments": [
                    {
                        "article_id": 100,
                        "attachment_id": 10,
                        "filename": "invoice.pdf",
                        "size": 12345,
                        "content_type": "application/pdf",
                    }
                ],
            },
            {
                "id": 101,
                "created_at": "2024-01-01T11:00:00Z",
                "internal": True,
                "sender": "agent1@acme.invalid",
                "subject": "Internal note",
                "body_html": "<p>Internal note.</p>",
                "body_text": "Internal note.",
                "attachments": [],
            },
        ],
    }

    snapshot = Snapshot.model_validate(snapshot_dict)
    pdf_bytes = render_pdf(snapshot, "default")

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 5_000


def test_render_pdf_does_not_emit_pydyf_identifier_deprecation_warning() -> None:
    snapshot = Snapshot.model_validate(
        {
            "ticket": {
                "id": 2,
                "number": "T2",
                "title": "warning guard",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "tags": ["pdf:sign"],
                "custom_fields": {"archive_path": ["A"], "archive_user_mode": "owner"},
            },
            "articles": [],
        }
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pdf_bytes = render_pdf(snapshot, "default")

    assert pdf_bytes.startswith(b"%PDF")
    assert not any(
        (
            isinstance(item.message, DeprecationWarning)
            and "PDF objects don’t take version or identifier" in str(item.message)
        )
        for item in caught
    )


def test_render_pdf_default_template_avoids_ignored_css_warnings(caplog) -> None:
    snapshot = Snapshot.model_validate(
        {
            "ticket": {
                "id": 3,
                "number": "T3",
                "title": "css warning guard",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "tags": ["pdf:sign"],
                "custom_fields": {"archive_path": ["A"], "archive_user_mode": "owner"},
            },
            "articles": [],
        }
    )

    with caplog.at_level(logging.WARNING, logger="weasyprint"):
        pdf_bytes = render_pdf(snapshot, "default")

    assert pdf_bytes.startswith(b"%PDF")
    assert not any(
        "Ignored `" in rec.getMessage() and "invalid value" in rec.getMessage()
        for rec in caplog.records
    )
    assert not any(
        "Ignored `" in rec.getMessage() and "unknown property" in rec.getMessage()
        for rec in caplog.records
    )
