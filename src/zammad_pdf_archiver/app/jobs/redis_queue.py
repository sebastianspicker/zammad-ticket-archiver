from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from dataclasses import dataclass
from typing import Any

import structlog

from zammad_pdf_archiver.app.jobs.history import record_history_event
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.observability.metrics import (
    queue_dlq_total,
    queue_enqueued_total,
    queue_failed_total,
    queue_processed_total,
    queue_retried_total,
)

log = structlog.get_logger(__name__)

_REDIS_CLIENTS: dict[str, Any] = {}
_REDIS_LOCK = asyncio.Lock()

_WORKER_TASKS: dict[str, asyncio.Task[None]] = {}
_WORKER_STOPS: dict[str, asyncio.Event] = {}
_CLAIM_IDLE_MS = 30_000


def _backend(settings: Settings) -> str:
    return (settings.workflow.execution_backend or "inprocess").strip().lower()


def _worker_key(settings: Settings) -> str:
    return "|".join(
        [
            settings.workflow.redis_url or "",
            settings.workflow.queue_stream,
            settings.workflow.queue_group,
        ]
    )


def _consumer_name(settings: Settings) -> str:
    configured = settings.workflow.queue_consumer
    if configured and configured.strip():
        return configured.strip()
    return f"{socket.gethostname()}-{os.getpid()}"


def _as_str(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _parse_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(_as_str(value))
    except Exception:
        return default


def _parse_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(_as_str(value))
    except Exception:
        return default


def _import_redis() -> tuple[Any, Any]:
    try:
        from redis.asyncio import Redis
        from redis.exceptions import ResponseError
    except ImportError as exc:
        raise RuntimeError(
            "Redis queue backend requires the redis package. "
            "Install with: pip install zammad-pdf-archiver[redis]"
        ) from exc
    return Redis, ResponseError


async def _get_redis(settings: Settings) -> Any:
    redis_url = settings.workflow.redis_url
    if not redis_url or not redis_url.strip():
        raise RuntimeError("workflow.redis_url is required for redis queue backend")

    async with _REDIS_LOCK:
        cached = _REDIS_CLIENTS.get(redis_url)
        if cached is not None:
            return cached

        Redis, _ = _import_redis()
        client = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
        )
        _REDIS_CLIENTS[redis_url] = client
        return client


@dataclass(frozen=True)
class _QueueEnvelope:
    message_id: str
    payload: dict[str, Any]
    delivery_id: str | None
    attempt: int
    not_before_ts: float
    last_error: str | None


def _decode_envelope(message_id: Any, raw_fields: dict[Any, Any]) -> _QueueEnvelope:
    fields = {_as_str(key): value for key, value in raw_fields.items()}
    payload_raw = _as_str(fields.get("payload_json", "{}"))
    payload = json.loads(payload_raw)
    if not isinstance(payload, dict):
        raise ValueError("payload_json is not an object")

    delivery_id_raw = _as_str(fields.get("delivery_id", "")).strip()
    last_error_raw = _as_str(fields.get("last_error", "")).strip()
    return _QueueEnvelope(
        message_id=_as_str(message_id),
        payload=payload,
        delivery_id=delivery_id_raw or None,
        attempt=max(0, _parse_int(fields.get("attempt"), default=0)),
        not_before_ts=max(0.0, _parse_float(fields.get("not_before_ts"), default=0.0)),
        last_error=last_error_raw or None,
    )


async def enqueue_ticket_job(
    *,
    delivery_id: str | None,
    payload: dict[str, Any],
    settings: Settings,
    attempt: int = 0,
    not_before_ts: float = 0.0,
    last_error: str | None = None,
) -> str:
    redis = await _get_redis(settings)
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    fields: dict[str, str] = {
        "payload_json": payload_json,
        "delivery_id": delivery_id or "",
        "attempt": str(max(0, int(attempt))),
        "not_before_ts": str(max(0.0, float(not_before_ts))),
        "enqueued_at": str(time.time()),
    }
    if last_error:
        fields["last_error"] = last_error[:500]
    message_id = await redis.xadd(settings.workflow.queue_stream, fields)
    queue_enqueued_total.inc()
    return _as_str(message_id)


async def _ack_and_delete(redis: Any, *, stream: str, group: str, message_id: str) -> None:
    try:
        await redis.xack(stream, group, message_id)
    finally:
        await redis.xdel(stream, message_id)


async def _push_dlq(
    redis: Any,
    *,
    settings: Settings,
    envelope: _QueueEnvelope,
    reason: str,
    error_message: str | None = None,
) -> None:
    fields: dict[str, str] = {
        "payload_json": json.dumps(
            envelope.payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        "delivery_id": envelope.delivery_id or "",
        "attempt": str(envelope.attempt),
        "reason": reason,
        "failed_at": str(time.time()),
    }
    if error_message:
        fields["error"] = error_message[:500]
    await redis.xadd(settings.workflow.queue_dlq_stream, fields)
    queue_dlq_total.inc()


async def _ensure_group(redis: Any, *, stream: str, group: str) -> None:
    _, ResponseError = _import_redis()
    try:
        # Start at 0 so backlog existing before group creation is visible to consumers.
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as exc:
        # BUSYGROUP Consumer Group name already exists
        if "BUSYGROUP" not in str(exc):
            raise


def _retry_delay_seconds(settings: Settings, *, attempt: int) -> float:
    base = float(settings.workflow.queue_retry_backoff_seconds)
    return base * (2**max(0, attempt))


async def _handle_envelope(
    redis: Any, *, settings: Settings, envelope: _QueueEnvelope
) -> float:
    stream = settings.workflow.queue_stream
    group = settings.workflow.queue_group

    now = time.time()
    if envelope.not_before_ts > now:
        # Keep the message pending; worker revisits pending entries without creating
        # stream churn from repeated ack+re-enqueue cycles.
        return envelope.not_before_ts - now

    try:
        result = await process_ticket(envelope.delivery_id, envelope.payload, settings)
        status = getattr(result, "status", "processed")
        message = getattr(result, "message", "")
    except Exception as exc:  # pragma: no cover - defensive fallback
        queue_failed_total.inc()
        status = "failed_transient"
        message = f"{exc.__class__.__name__}: {exc}"

    if status == "failed_transient":
        if envelope.attempt < settings.workflow.queue_retry_max_attempts:
            delay = _retry_delay_seconds(settings, attempt=envelope.attempt)
            await enqueue_ticket_job(
                delivery_id=envelope.delivery_id,
                payload=envelope.payload,
                settings=settings,
                attempt=envelope.attempt + 1,
                not_before_ts=time.time() + delay,
                last_error=message or envelope.last_error,
            )
            queue_retried_total.inc()
        else:
            await _push_dlq(
                redis,
                settings=settings,
                envelope=envelope,
                reason="retry_exhausted",
                error_message=message or envelope.last_error,
            )
    elif status == "failed_permanent":
        await _push_dlq(
            redis,
            settings=settings,
            envelope=envelope,
            reason="permanent_error",
            error_message=message or envelope.last_error,
        )
    else:
        queue_processed_total.inc()

    await _ack_and_delete(redis, stream=stream, group=group, message_id=envelope.message_id)
    return 0.0


def _merge_min_delay(current: float | None, candidate: float | None) -> float | None:
    if candidate is None or candidate <= 0:
        return current
    if current is None or candidate < current:
        return candidate
    return current


def _extract_stream_messages(records: Any) -> list[tuple[Any, Any]]:
    out: list[tuple[Any, Any]] = []
    if not isinstance(records, list):
        return out

    for record in records:
        if not isinstance(record, (list, tuple)) or len(record) != 2:
            continue
        _stream_name, messages = record
        if not isinstance(messages, list):
            continue
        for message in messages:
            if isinstance(message, (list, tuple)) and len(message) == 2:
                out.append((message[0], message[1]))
    return out


def _extract_claimed_messages(records: Any) -> list[tuple[Any, Any]]:
    out: list[tuple[Any, Any]] = []
    if not isinstance(records, list):
        return out
    for message in records:
        if isinstance(message, (list, tuple)) and len(message) == 2:
            out.append((message[0], message[1]))
    return out


def _pending_entry_field(entry: Any, key: str) -> Any:
    if isinstance(entry, dict):
        return entry.get(key)
    return getattr(entry, key, None)


async def _claim_stale_pending(
    redis: Any,
    *,
    stream: str,
    group: str,
    consumer: str,
    count: int,
    min_idle_ms: int = _CLAIM_IDLE_MS,
) -> list[tuple[Any, Any]]:
    try:
        pending_entries = await redis.xpending_range(stream, group, "-", "+", count)
    except Exception:
        return []

    message_ids: list[str] = []
    for entry in pending_entries:
        message_id = _as_str(_pending_entry_field(entry, "message_id") or "").strip()
        owner = _as_str(_pending_entry_field(entry, "consumer") or "").strip()
        idle_ms = _parse_int(_pending_entry_field(entry, "time_since_delivered"), default=0)
        if not message_id:
            continue
        if owner == consumer:
            continue
        if idle_ms < min_idle_ms:
            continue
        message_ids.append(message_id)

    if not message_ids:
        return []

    claimed = await redis.xclaim(stream, group, consumer, min_idle_ms, message_ids)
    return _extract_claimed_messages(claimed)


async def _read_own_pending(
    redis: Any,
    *,
    stream: str,
    group: str,
    consumer: str,
    count: int,
) -> list[tuple[Any, Any]]:
    records = await redis.xreadgroup(
        groupname=group,
        consumername=consumer,
        streams={stream: "0"},
        count=count,
        block=1,
    )
    return _extract_stream_messages(records)


async def _read_new_messages(
    redis: Any,
    *,
    stream: str,
    group: str,
    consumer: str,
    count: int,
    block_ms: int,
) -> list[tuple[Any, Any]]:
    records = await redis.xreadgroup(
        groupname=group,
        consumername=consumer,
        streams={stream: ">"},
        count=count,
        block=block_ms,
    )
    return _extract_stream_messages(records)


async def _process_messages(
    redis: Any,
    *,
    settings: Settings,
    messages: list[tuple[Any, Any]],
) -> float | None:
    stream = settings.workflow.queue_stream
    group = settings.workflow.queue_group
    min_delay: float | None = None

    for message_id, raw_fields in messages:
        try:
            envelope = _decode_envelope(message_id, raw_fields)
        except Exception as exc:
            queue_failed_total.inc()
            envelope = _QueueEnvelope(
                message_id=_as_str(message_id),
                payload={},
                delivery_id=None,
                attempt=0,
                not_before_ts=0.0,
                last_error=str(exc),
            )
            await _push_dlq(
                redis,
                settings=settings,
                envelope=envelope,
                reason="invalid_message",
                error_message=str(exc),
            )
            await record_history_event(
                settings,
                status="failed_permanent",
                ticket_id=None,
                classification="Permanent",
                message=f"invalid_message: {exc}",
                delivery_id=None,
                request_id=None,
            )
            await _ack_and_delete(redis, stream=stream, group=group, message_id=envelope.message_id)
            continue

        try:
            delay = await _handle_envelope(redis, settings=settings, envelope=envelope)
            min_delay = _merge_min_delay(min_delay, delay)
        except Exception:
            queue_failed_total.inc()
            log.exception(
                "queue.worker.handle_message_failed",
                message_id=envelope.message_id,
            )

    return min_delay


async def _worker_loop(settings: Settings, stop_event: asyncio.Event) -> None:
    redis = await _get_redis(settings)
    stream = settings.workflow.queue_stream
    group = settings.workflow.queue_group
    consumer = _consumer_name(settings)
    await _ensure_group(redis, stream=stream, group=group)

    while not stop_event.is_set():
        try:
            min_delay: float | None = None

            claimed = await _claim_stale_pending(
                redis,
                stream=stream,
                group=group,
                consumer=consumer,
                count=settings.workflow.queue_read_count,
            )
            min_delay = _merge_min_delay(
                min_delay,
                await _process_messages(redis, settings=settings, messages=claimed),
            )

            pending = await _read_own_pending(
                redis,
                stream=stream,
                group=group,
                consumer=consumer,
                count=settings.workflow.queue_read_count,
            )
            min_delay = _merge_min_delay(
                min_delay,
                await _process_messages(redis, settings=settings, messages=pending),
            )

            block_ms = 1 if (claimed or pending) else settings.workflow.queue_read_block_ms
            new_messages = await _read_new_messages(
                redis,
                stream=stream,
                group=group,
                consumer=consumer,
                count=settings.workflow.queue_read_count,
                block_ms=block_ms,
            )
            min_delay = _merge_min_delay(
                min_delay,
                await _process_messages(redis, settings=settings, messages=new_messages),
            )

            if min_delay is not None and min_delay > 0:
                await asyncio.sleep(min(min_delay, 1.0))
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception:
            log.exception("queue.worker.loop_error")
            await asyncio.sleep(0.3)


async def start_queue_worker(settings: Settings) -> asyncio.Task[None] | None:
    if _backend(settings) != "redis_queue":
        return None

    key = _worker_key(settings)
    existing = _WORKER_TASKS.get(key)
    if existing is not None and not existing.done():
        return existing

    stop_event = asyncio.Event()
    task = asyncio.create_task(_worker_loop(settings, stop_event), name=f"redis-queue-worker:{key}")
    _WORKER_STOPS[key] = stop_event
    _WORKER_TASKS[key] = task
    return task


async def stop_queue_worker(settings: Settings, *, timeout: float = 3.0) -> None:
    key = _worker_key(settings)
    stop_event = _WORKER_STOPS.get(key)
    task = _WORKER_TASKS.get(key)
    if stop_event is None or task is None:
        return

    stop_event.set()
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except TimeoutError:
        task.cancel()
        try:
            await task
        except Exception:
            pass
    finally:
        _WORKER_STOPS.pop(key, None)
        _WORKER_TASKS.pop(key, None)


def _pending_count(raw: Any) -> int:
    if isinstance(raw, dict):
        value = raw.get("pending")
        if isinstance(value, int):
            return value
    value = getattr(raw, "pending", None)
    if isinstance(value, int):
        return value
    return 0


async def get_queue_stats(settings: Settings) -> dict[str, Any]:
    execution_backend = _backend(settings)
    if execution_backend != "redis_queue":
        return {
            "execution_backend": execution_backend,
            "queue_enabled": False,
        }

    redis = await _get_redis(settings)
    stream = settings.workflow.queue_stream
    group = settings.workflow.queue_group
    dlq_stream = settings.workflow.queue_dlq_stream
    await _ensure_group(redis, stream=stream, group=group)
    queue_depth = int(await redis.xlen(stream))
    dlq_depth = int(await redis.xlen(dlq_stream))
    pending_raw = await redis.xpending(stream, group)

    return {
        "execution_backend": execution_backend,
        "queue_enabled": True,
        "stream": stream,
        "group": group,
        "consumer": _consumer_name(settings),
        "queue_depth": queue_depth,
        "pending": _pending_count(pending_raw),
        "dlq_stream": dlq_stream,
        "dlq_depth": dlq_depth,
        "retry_max_attempts": settings.workflow.queue_retry_max_attempts,
        "history_stream": settings.workflow.history_stream,
        "history_retention_maxlen": settings.workflow.history_retention_maxlen,
    }


async def drain_dlq(settings: Settings, *, limit: int = 100) -> int:
    if limit < 1:
        return 0
    bounded_limit = min(int(limit), 1000)

    redis = await _get_redis(settings)
    dlq_stream = settings.workflow.queue_dlq_stream
    entries = await redis.xrange(dlq_stream, min="-", max="+", count=bounded_limit)
    if not entries:
        return 0

    ids = [_as_str(entry_id) for entry_id, _ in entries]
    if not ids:
        return 0

    pipeline = redis.pipeline(transaction=False)
    for entry_id in ids:
        pipeline.xdel(dlq_stream, entry_id)
    await pipeline.execute()
    return len(ids)


async def aclose_queue_clients() -> None:
    async with _REDIS_LOCK:
        clients = list(_REDIS_CLIENTS.values())
        _REDIS_CLIENTS.clear()

    for client in clients:
        try:
            await client.aclose()
        except Exception:
            log.warning("queue.redis_close_failed")
