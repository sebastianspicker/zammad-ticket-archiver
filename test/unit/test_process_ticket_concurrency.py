from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import zammad_pdf_archiver.app.jobs.process_ticket as process_ticket_module
from zammad_pdf_archiver.adapters.zammad.models import TagList
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.config.settings import Settings


def _settings(storage_root: Path) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": str(storage_root)},
            "hardening": {"webhook": {"allow_unsigned": True}},
        }
    )


def test_process_ticket_serializes_same_ticket_concurrent_runs(
    monkeypatch, tmp_path: Path
) -> None:
    process_ticket_module._DELIVERY_ID_SETS.clear()
    process_ticket_module._IN_FLIGHT_TICKETS.clear()

    class _FakeClient:
        _tags: set[str] = {"pdf:sign"}
        _notes_written = 0

        def __init__(self, **kwargs) -> None:  # noqa: ANN003, ARG002
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001, D401
            return None

        async def get_ticket(self, ticket_id: int) -> SimpleNamespace:
            return SimpleNamespace(
                id=ticket_id,
                number="12345",
                title="concurrency",
                owner=SimpleNamespace(login="owner.user"),
                updated_by=SimpleNamespace(login="agent.user"),
                preferences=SimpleNamespace(
                    custom_fields={
                        "archive_path": "Support > Team",
                        "archive_user_mode": "owner",
                    }
                ),
            )

        async def list_tags(self, ticket_id: int) -> TagList:  # noqa: ARG002
            # Snapshot before yielding: two concurrent calls both see trigger tag.
            snapshot = sorted(type(self)._tags)
            await asyncio.sleep(0.05)
            return TagList(snapshot)

        async def remove_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            type(self)._tags.discard(tag)

        async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            type(self)._tags.add(tag)

        async def list_articles(self, ticket_id: int) -> list[SimpleNamespace]:  # noqa: ARG002
            return []

        async def create_internal_article(
            self, ticket_id: int, subject: str, body_html: str  # noqa: ARG002
        ) -> SimpleNamespace:
            type(self)._notes_written += 1
            return SimpleNamespace(id=type(self)._notes_written)

    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.AsyncZammadClient",
        _FakeClient,
    )

    async def _fake_build_snapshot(client, ticket_id, *, ticket=None, tags=None):  # noqa: ANN001, ARG001
        return object()

    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.build_snapshot",
        _fake_build_snapshot,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.render_pdf",
        lambda snapshot, template, max_articles=None: b"%PDF-1.7\n%%EOF\n",  # noqa: ARG005
    )

    settings = _settings(tmp_path)
    payload = {"ticket": {"id": 123}}

    async def _run() -> None:
        await asyncio.gather(
            process_ticket("d-1", payload, settings),
            process_ticket("d-2", payload, settings),
        )

    asyncio.run(_run())

    assert _FakeClient._notes_written == 1
