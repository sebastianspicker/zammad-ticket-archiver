from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import zammad_pdf_archiver.app.jobs.process_ticket as process_ticket_module
from zammad_pdf_archiver.adapters.zammad.models import TagList
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.errors import TransientError


def _settings(storage_root: Path) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": str(storage_root)},
            "hardening": {"webhook": {"allow_unsigned": True}},
        }
    )


def test_skipped_inflight_delivery_id_is_not_poisoned_for_retry(
    monkeypatch, tmp_path: Path
) -> None:
    process_ticket_module._DELIVERY_ID_SETS.clear()
    process_ticket_module._IN_FLIGHT_TICKETS.clear()

    class _FakeClient:
        _tags: set[str] = {"pdf:sign"}
        _success_notes = 0
        _error_notes = 0

        def __init__(self, **kwargs) -> None:  # noqa: ANN003, ARG002
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        async def get_ticket(self, ticket_id: int) -> SimpleNamespace:
            return SimpleNamespace(
                id=ticket_id,
                number="12345",
                title="idempotency",
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
            await asyncio.sleep(0.05)
            return TagList(sorted(type(self)._tags))

        async def remove_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            type(self)._tags.discard(tag)

        async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            type(self)._tags.add(tag)

        async def list_articles(self, ticket_id: int) -> list[SimpleNamespace]:  # noqa: ARG002
            return []

        async def create_internal_article(
            self, ticket_id: int, subject: str, body_html: str  # noqa: ARG002
        ) -> SimpleNamespace:
            if "archiver error" in subject:
                type(self)._error_notes += 1
            if "PDF archived" in subject:
                type(self)._success_notes += 1
            return SimpleNamespace(id=type(self)._error_notes + type(self)._success_notes)

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

    calls = {"n": 0}

    def _flaky_render(snapshot, template, max_articles=None):  # noqa: ANN001, ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            raise TransientError("transient-render-failure")
        return b"%PDF-1.7\n%%EOF\n"

    monkeypatch.setattr("zammad_pdf_archiver.app.jobs.process_ticket.render_pdf", _flaky_render)

    settings = _settings(tmp_path)
    payload = {"ticket": {"id": 321}}

    async def _run_concurrent_once() -> None:
        await asyncio.gather(
            process_ticket("d-1", payload, settings),
            process_ticket("d-2", payload, settings),
        )

    asyncio.run(_run_concurrent_once())

    # Retry delivery d-2 after the in-flight run is over.
    asyncio.run(process_ticket("d-2", payload, settings))

    # Expected: first run writes one error note; retry run succeeds and writes one success note.
    assert _FakeClient._error_notes == 1
    assert _FakeClient._success_notes == 1
