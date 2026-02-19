from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import zammad_pdf_archiver.app.jobs.process_ticket as process_ticket_module
from zammad_pdf_archiver.adapters.zammad.models import TagList
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.errors import TransientError
from zammad_pdf_archiver.domain.state_machine import PROCESSING_TAG


def _settings(storage_root: Path) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": str(storage_root)},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": True,
                }
            },
        }
    )


def test_process_ticket_logs_processing_tag_cleanup_failures(
    monkeypatch, tmp_path: Path
) -> None:
    process_ticket_module._DELIVERY_ID_SETS.clear()
    process_ticket_module._IN_FLIGHT_TICKETS.clear()

    class _FakeClient:
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
                title="cleanup",
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
            return TagList(["pdf:sign"])

        async def remove_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            if tag == PROCESSING_TAG:
                raise RuntimeError("cleanup-remove-failed")

        async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
            return None

        async def list_articles(self, ticket_id: int) -> list[SimpleNamespace]:  # noqa: ARG002
            return []

        async def create_internal_article(
            self, ticket_id: int, subject: str, body_html: str  # noqa: ARG002
        ) -> SimpleNamespace:
            return SimpleNamespace(id=1)

    class _CapturingLog:
        def __init__(self) -> None:
            self.exception_events: list[str] = []

        def info(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            return None

        def exception(self, event: str, **kwargs) -> None:  # noqa: ANN003
            self.exception_events.append(event)

    async def _fake_build_snapshot(client, ticket_id, *, ticket=None, tags=None):  # noqa: ANN001, ARG001
        return object()

    async def _fake_apply_error(
        client, ticket_id: int, *, keep_trigger: bool = True, trigger_tag: str = "pdf:sign"  # noqa: ANN001, ARG001
    ) -> None:
        return None

    def _raise_transient(*args, **kwargs) -> bytes:  # noqa: ANN002, ANN003
        raise TransientError("render-failed")

    capturing_log = _CapturingLog()
    monkeypatch.setattr(process_ticket_module, "log", capturing_log)
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.AsyncZammadClient",
        _FakeClient,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.build_snapshot",
        _fake_build_snapshot,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.apply_error",
        _fake_apply_error,
    )
    monkeypatch.setattr("zammad_pdf_archiver.app.jobs.process_ticket.render_pdf", _raise_transient)

    settings = _settings(tmp_path)
    payload = {"ticket": {"id": 321}}

    asyncio.run(process_ticket("d-cleanup-log-1", payload, settings))

    assert "process_ticket.processing_tag_cleanup_failed" in capturing_log.exception_events
