from __future__ import annotations

import json
import time
from typing import Any

import structlog

from zammad_pdf_archiver.config.redact import scrub_secrets_in_text
from zammad_pdf_archiver.config.settings import Settings

log = structlog.get_logger(__name__)


def _import_redis() -> Any:
    try:
        from redis.asyncio import Redis
    except ImportError:
        return None
    return Redis


def _history_enabled(settings: Settings) -> bool:
    if not settings.workflow.redis_url:
        return False
    return int(settings.workflow.history_retention_maxlen) > 0


async def _redis_client(settings: Settings) -> Any | None:
    if not _history_enabled(settings):
        return None
    Redis = _import_redis()
    if Redis is None:
        return None
    return Redis.from_url(
        settings.workflow.redis_url,
        decode_responses=True,
        socket_timeout=5.0,
        socket_connect_timeout=5.0,
    )


def _bounded_message(message: str) -> str:
    cleaned = scrub_secrets_in_text((message or "").strip())
    if len(cleaned) > 500:
        return cleaned[:500]
    return cleaned


async def record_history_event(
    settings: Settings,
    *,
    status: str,
    ticket_id: int | None,
    classification: str | None = None,
    message: str = "",
    delivery_id: str | None = None,
    request_id: str | None = None,
) -> bool:
    redis = await _redis_client(settings)
    if redis is None:
        return False

    fields: dict[str, str] = {
        "status": status,
        "ticket_id": str(ticket_id) if ticket_id is not None else "",
        "classification": classification or "",
        "message": _bounded_message(message),
        "delivery_id": delivery_id or "",
        "request_id": request_id or "",
        "created_at": str(time.time()),
    }

    stream = settings.workflow.history_stream
    maxlen = int(settings.workflow.history_retention_maxlen)
    try:
        await redis.xadd(stream, fields, maxlen=maxlen, approximate=True)
        return True
    except Exception:
        log.warning("history.record_failed", status=status, ticket_id=ticket_id)
        return False
    finally:
        try:
            await redis.aclose()
        except Exception:
            pass


def _to_int(value: str | None, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except Exception:
        return default


def _to_float(value: str | None, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except Exception:
        return default


def _normalize_entry(message_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    status = str(fields.get("status", ""))
    ticket_id = _to_int(str(fields.get("ticket_id", "")), default=None)
    classification = str(fields.get("classification", "")).strip() or None
    message = str(fields.get("message", ""))
    delivery_id = str(fields.get("delivery_id", "")).strip() or None
    request_id = str(fields.get("request_id", "")).strip() or None
    created_at_ts = _to_float(str(fields.get("created_at", "")), default=0.0)

    return {
        "id": message_id,
        "status": status,
        "ticket_id": ticket_id,
        "classification": classification,
        "message": message,
        "delivery_id": delivery_id,
        "request_id": request_id,
        "created_at": created_at_ts,
    }


async def read_history(
    settings: Settings,
    *,
    limit: int,
    ticket_id: int | None = None,
) -> list[dict[str, Any]]:
    redis = await _redis_client(settings)
    if redis is None:
        return []

    bounded_limit = max(1, min(int(limit), 5000))
    # Over-fetch when filtering by ticket_id to avoid empty pages on sparse streams.
    fetch_count = bounded_limit if ticket_id is None else min(bounded_limit * 8, 10_000)

    try:
        entries = await redis.xrevrange(
            settings.workflow.history_stream,
            max="+",
            min="-",
            count=fetch_count,
        )
    except Exception:
        log.warning("history.read_failed")
        return []
    finally:
        try:
            await redis.aclose()
        except Exception:
            pass

    out: list[dict[str, Any]] = []
    for message_id, raw_fields in entries:
        fields = {str(k): v for k, v in raw_fields.items()}
        item = _normalize_entry(str(message_id), fields)
        if ticket_id is not None and item["ticket_id"] != ticket_id:
            continue
        out.append(item)
        if len(out) >= bounded_limit:
            break

    return out


async def read_history_json(
    settings: Settings,
    *,
    limit: int,
    ticket_id: int | None = None,
) -> str:
    payload = {
        "status": "ok",
        "count": 0,
        "items": [],
    }
    items = await read_history(settings, limit=limit, ticket_id=ticket_id)
    payload["count"] = len(items)
    payload["items"] = items
    return json.dumps(payload, indent=2)
