from __future__ import annotations

import time
from dataclasses import dataclass, field

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import redis_queue
from zammad_pdf_archiver.app.jobs.process_ticket import ProcessTicketResult


@dataclass
class _Pipeline:
    redis: _FakeRedis
    dels: list[tuple[str, str]] = field(default_factory=list)

    def xdel(self, stream: str, message_id: str) -> _Pipeline:
        self.dels.append((stream, message_id))
        return self

    async def execute(self) -> list[int]:
        for stream, message_id in self.dels:
            self.redis.deleted.append((stream, message_id))
        return [1 for _ in self.dels]


@dataclass
class _FakeRedis:
    xadds: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    acked: list[tuple[str, str, str]] = field(default_factory=list)
    deleted: list[tuple[str, str]] = field(default_factory=list)
    stream_lengths: dict[str, int] = field(default_factory=dict)
    pending: int = 0
    dlq_entries: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.xadds.append((stream, fields))
        self.stream_lengths[stream] = self.stream_lengths.get(stream, 0) + 1
        if stream.endswith(":dlq"):
            self.dlq_entries.append((f"{len(self.dlq_entries)+1}-0", fields))
        return f"{len(self.xadds)}-0"

    async def xack(self, stream: str, group: str, message_id: str) -> int:
        self.acked.append((stream, group, message_id))
        return 1

    async def xdel(self, stream: str, message_id: str) -> int:
        self.deleted.append((stream, message_id))
        return 1

    async def xlen(self, stream: str) -> int:
        return self.stream_lengths.get(stream, 0)

    async def xpending(self, stream: str, group: str) -> dict[str, int]:
        return {"pending": self.pending}

    async def xrange(
        self,
        stream: str,
        min: str,
        max: str,
        count: int,
    ) -> list[tuple[str, dict[str, str]]]:  # noqa: A002
        return self.dlq_entries[:count]

    def pipeline(self, transaction: bool = False) -> _Pipeline:  # noqa: ARG002
        return _Pipeline(redis=self)

    async def aclose(self) -> None:
        return None


def test_get_queue_stats_inprocess_disabled(tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"execution_backend": "inprocess"}},
    )
    stats = redis_queue.asyncio.run(redis_queue.get_queue_stats(settings))
    assert stats == {"execution_backend": "inprocess", "queue_enabled": False}


def test_handle_envelope_transient_requeues(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost/0",
                "queue_retry_max_attempts": 2,
                "queue_retry_backoff_seconds": 1.0,
            }
        },
    )
    fake = _FakeRedis()

    async def _stub_process_ticket(delivery_id, payload, settings):  # noqa: ANN001, ARG001
        return ProcessTicketResult(
            status="failed_transient",
            ticket_id=payload.get("ticket_id"),
            message="tmp",
        )

    async def _stub_enqueue_ticket_job(  # noqa: ANN001
        *,
        delivery_id,
        payload,
        settings,
        attempt,
        not_before_ts,
        last_error,
    ) -> str:
        fields = {
            "payload_json": "{}",
            "delivery_id": delivery_id or "",
            "attempt": str(attempt),
            "not_before_ts": str(not_before_ts),
            "last_error": last_error or "",
        }
        return await fake.xadd(settings.workflow.queue_stream, fields)

    monkeypatch.setattr(redis_queue, "process_ticket", _stub_process_ticket)
    monkeypatch.setattr(redis_queue, "enqueue_ticket_job", _stub_enqueue_ticket_job)
    envelope = redis_queue._QueueEnvelope(  # noqa: SLF001
        message_id="1-0",
        payload={"ticket_id": 123},
        delivery_id="d-1",
        attempt=0,
        not_before_ts=0.0,
        last_error=None,
    )

    redis_queue.asyncio.run(
        redis_queue._handle_envelope(fake, settings=settings, envelope=envelope)  # noqa: SLF001
    )

    assert any(stream == settings.workflow.queue_stream for stream, _ in fake.xadds)
    retry_entry = next(
        fields
        for stream, fields in fake.xadds
        if stream == settings.workflow.queue_stream
    )
    assert retry_entry["attempt"] == "1"
    assert float(retry_entry["not_before_ts"]) >= time.time() - 0.5
    assert fake.acked == [(settings.workflow.queue_stream, settings.workflow.queue_group, "1-0")]
    assert fake.deleted[-1] == (settings.workflow.queue_stream, "1-0")


def test_handle_envelope_permanent_moves_to_dlq(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost/0",
                "queue_dlq_stream": "zammad:jobs:dlq",
            }
        },
    )
    fake = _FakeRedis()

    async def _stub_process_ticket(delivery_id, payload, settings):  # noqa: ANN001, ARG001
        return ProcessTicketResult(
            status="failed_permanent",
            ticket_id=payload.get("ticket_id"),
            message="perm",
        )

    monkeypatch.setattr(redis_queue, "process_ticket", _stub_process_ticket)
    envelope = redis_queue._QueueEnvelope(  # noqa: SLF001
        message_id="2-0",
        payload={"ticket_id": 321},
        delivery_id="d-2",
        attempt=0,
        not_before_ts=0.0,
        last_error=None,
    )

    redis_queue.asyncio.run(
        redis_queue._handle_envelope(fake, settings=settings, envelope=envelope)  # noqa: SLF001
    )

    assert any(stream == settings.workflow.queue_dlq_stream for stream, _ in fake.xadds)
    assert fake.acked == [(settings.workflow.queue_stream, settings.workflow.queue_group, "2-0")]
    assert fake.deleted[-1] == (settings.workflow.queue_stream, "2-0")


def test_drain_dlq_respects_limit(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost/0"}},
    )
    fake = _FakeRedis(
        dlq_entries=[
            ("1-0", {"payload_json": "{}"}),
            ("2-0", {"payload_json": "{}"}),
            ("3-0", {"payload_json": "{}"}),
        ]
    )

    async def _stub_get_redis(_settings):  # noqa: ANN001
        return fake

    monkeypatch.setattr(redis_queue, "_get_redis", _stub_get_redis)
    drained = redis_queue.asyncio.run(redis_queue.drain_dlq(settings, limit=2))
    assert drained == 2
    assert fake.deleted == [
        (settings.workflow.queue_dlq_stream, "1-0"),
        (settings.workflow.queue_dlq_stream, "2-0"),
    ]


def test_handle_envelope_not_before_is_deferred_without_reenqueue(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost/0",
            }
        },
    )
    fake = _FakeRedis()

    async def _stub_process_ticket(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("process_ticket should not run before not_before_ts")

    monkeypatch.setattr(redis_queue, "process_ticket", _stub_process_ticket)
    envelope = redis_queue._QueueEnvelope(  # noqa: SLF001
        message_id="3-0",
        payload={"ticket_id": 123},
        delivery_id="d-3",
        attempt=1,
        not_before_ts=time.time() + 60,
        last_error="tmp",
    )

    defer_seconds = redis_queue.asyncio.run(  # noqa: SLF001
        redis_queue._handle_envelope(fake, settings=settings, envelope=envelope)
    )

    assert defer_seconds > 0
    assert fake.xadds == []
    assert fake.acked == []
    assert fake.deleted == []


def test_ensure_group_replays_existing_backlog() -> None:
    class _GroupRedis:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str, bool]] = []

        async def xgroup_create(self, stream: str, group: str, id: str, mkstream: bool) -> None:  # noqa: A002
            self.calls.append((stream, group, id, mkstream))

    fake = _GroupRedis()
    redis_queue.asyncio.run(redis_queue._ensure_group(fake, stream="s", group="g"))  # noqa: SLF001

    assert fake.calls == [("s", "g", "0", True)]


def test_claim_stale_pending_reassigns_messages() -> None:
    class _ClaimRedis:
        def __init__(self) -> None:
            self.claim_ids: list[str] = []

        async def xpending_range(self, stream, group, min, max, count):  # noqa: ANN001, A002
            return [
                {
                    "message_id": "1-0",
                    "consumer": "worker-a",
                    "time_since_delivered": 45_000,
                    "times_delivered": 1,
                },
                {
                    "message_id": "2-0",
                    "consumer": "worker-b",
                    "time_since_delivered": 5_000,
                    "times_delivered": 1,
                },
                {
                    "message_id": "3-0",
                    "consumer": "worker-c",
                    "time_since_delivered": 35_000,
                    "times_delivered": 2,
                },
                {
                    "message_id": "4-0",
                    "consumer": "worker-new",
                    "time_since_delivered": 55_000,
                    "times_delivered": 3,
                },
            ]

        async def xclaim(  # noqa: ANN001
            self, stream, group, consumer, min_idle_ms, message_ids
        ):
            self.claim_ids = list(message_ids)
            return [(message_id, {"payload_json": "{}"}) for message_id in message_ids]

    fake = _ClaimRedis()
    messages = redis_queue.asyncio.run(  # noqa: SLF001
        redis_queue._claim_stale_pending(
            fake,
            stream="stream-1",
            group="group-1",
            consumer="worker-new",
            count=10,
            min_idle_ms=30_000,
        )
    )

    assert fake.claim_ids == ["1-0", "3-0"]
    assert messages == [("1-0", {"payload_json": "{}"}), ("3-0", {"payload_json": "{}"})]
