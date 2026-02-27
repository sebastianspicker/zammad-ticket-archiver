from __future__ import annotations

import asyncio

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import history


class _FakeRedis:
    def __init__(self) -> None:
        self.xadd_calls: list[tuple[str, dict[str, str], int | None, bool]] = []
        self.entries: list[tuple[str, dict[str, str]]] = []

    async def xadd(
        self,
        stream: str,
        fields: dict[str, str],
        maxlen: int | None = None,
        approximate: bool = True,
    ):
        self.xadd_calls.append((stream, fields, maxlen, approximate))
        return "1-0"

    async def xrevrange(self, stream: str, max: str, min: str, count: int):  # noqa: A002
        return self.entries[:count]

    async def aclose(self) -> None:
        return None


def test_record_history_event_no_redis_url(tmp_path) -> None:
    settings = make_settings(str(tmp_path))
    ok = asyncio.run(
        history.record_history_event(
            settings,
            status="processed",
            ticket_id=1,
        )
    )
    assert ok is False


def test_record_history_event_writes_stream(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"redis_url": "redis://localhost/0"}},
    )
    fake = _FakeRedis()

    async def _stub_client(_settings):
        return fake

    monkeypatch.setattr(history, "_redis_client", _stub_client)

    ok = asyncio.run(
        history.record_history_event(
            settings,
            status="processed",
            ticket_id=123,
            request_id="req-1",
        )
    )
    assert ok is True
    assert len(fake.xadd_calls) == 1
    stream, fields, maxlen, approx = fake.xadd_calls[0]
    assert stream == settings.workflow.history_stream
    assert fields["status"] == "processed"
    assert fields["ticket_id"] == "123"
    assert maxlen == settings.workflow.history_retention_maxlen
    assert approx is True


def test_read_history_filters_ticket(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"redis_url": "redis://localhost/0"}},
    )
    fake = _FakeRedis()
    fake.entries = [
        ("2-0", {"status": "processed", "ticket_id": "5", "created_at": "1"}),
        ("1-0", {"status": "failed_permanent", "ticket_id": "7", "created_at": "2"}),
    ]

    async def _stub_client(_settings):
        return fake

    monkeypatch.setattr(history, "_redis_client", _stub_client)

    items = asyncio.run(history.read_history(settings, limit=10, ticket_id=7))
    assert len(items) == 1
    assert items[0]["ticket_id"] == 7
    assert items[0]["status"] == "failed_permanent"


def test_record_history_event_redacts_sensitive_message(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"redis_url": "redis://localhost/0"}},
    )
    fake = _FakeRedis()

    async def _stub_client(_settings):
        return fake

    monkeypatch.setattr(history, "_redis_client", _stub_client)

    asyncio.run(
        history.record_history_event(
            settings,
            status="failed_permanent",
            ticket_id=123,
            message="Authorization: Bearer supersecret token=abc123",
        )
    )

    assert len(fake.xadd_calls) == 1
    _, fields, _, _ = fake.xadd_calls[0]
    assert fields["message"] == "Authorization: Bearer [redacted] token=[redacted]"
