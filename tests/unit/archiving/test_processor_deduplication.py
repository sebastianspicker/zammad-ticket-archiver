"""Verify process-local delivery deduplication preserves job metadata."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import chronikwerk.archiving.processor as processor
from chronikwerk.operations.job import REQUEST_ID_KEY


def test_seen_delivery_skips_before_opening_a_zammad_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history: list[tuple[str, int, str | None, str | None]] = []
    released: list[int] = []
    options = SimpleNamespace(workflow=SimpleNamespace(delivery_id_ttl_seconds=120))

    async def acquire(ticket_id: int) -> bool:
        assert ticket_id == 42
        return True

    async def claim(_ttl: int, delivery_id: str) -> bool:
        assert delivery_id == "delivery-42"
        return False

    async def release(ticket_id: int) -> None:
        released.append(ticket_id)

    def record(attempt: object, *, status: str, **_kwargs: object) -> None:
        history.append(
            (
                status,
                attempt.ticket_id,  # type: ignore[attr-defined]
                attempt.delivery_id,  # type: ignore[attr-defined]
                attempt.request_id,  # type: ignore[attr-defined]
            )
        )

    monkeypatch.setattr(processor, "try_acquire_ticket", acquire)
    monkeypatch.setattr(processor, "try_claim_delivery_id", claim)
    monkeypatch.setattr(processor, "release_ticket", release)
    monkeypatch.setattr(processor, "record_history", record)

    outcome = asyncio.run(
        processor.process_ticket(
            "delivery-42",
            {"ticket_id": "42", REQUEST_ID_KEY: "request-42"},
            options,  # type: ignore[arg-type]
        )
    )

    assert outcome.status == "skipped_idempotency"
    assert outcome.ticket_id == 42
    assert history == [
        ("running", 42, "delivery-42", "request-42"),
        ("skipped_idempotency", 42, "delivery-42", "request-42"),
    ]
    assert released == [42]
