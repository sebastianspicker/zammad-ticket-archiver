"""Test the ordered public tag projections used by the archive workflow."""

from __future__ import annotations

import asyncio

from chronikwerk.zammad.workflow import apply_done, apply_error, apply_processing, should_process


class _TagClient:
    """Record sequential tag mutations without contacting Zammad."""

    def __init__(self) -> None:
        self.events: list[tuple[str, int, str]] = []

    async def remove_tag(self, ticket_id: int, tag: str) -> None:
        self.events.append(("remove", ticket_id, tag))

    async def add_tag(self, ticket_id: int, tag: str) -> None:
        self.events.append(("add", ticket_id, tag))


def test_force_reprocess_clears_done_before_processing_tag() -> None:
    client = _TagClient()

    asyncio.run(
        apply_processing(
            client,
            42,
            trigger_tag="archive:ready",
            force_reprocess=True,
        )
    )

    assert client.events == [
        ("remove", 42, "pdf:signed"),
        ("remove", 42, "pdf:error"),
        ("remove", 42, "archive:ready"),
        ("add", 42, "pdf:processing"),
    ]


def test_done_and_error_transitions_are_sequential_and_retry_safe() -> None:
    client = _TagClient()

    asyncio.run(apply_done(client, 42, trigger_tag="archive:ready"))
    asyncio.run(apply_error(client, 42, keep_trigger=True, trigger_tag="archive:ready"))
    asyncio.run(apply_error(client, 42, keep_trigger=False, trigger_tag="archive:ready"))

    assert client.events == [
        ("remove", 42, "pdf:processing"),
        ("remove", 42, "pdf:error"),
        ("remove", 42, "archive:ready"),
        ("add", 42, "pdf:signed"),
        ("remove", 42, "pdf:processing"),
        ("remove", 42, "pdf:signed"),
        ("add", 42, "archive:ready"),
        ("add", 42, "pdf:error"),
        ("remove", 42, "pdf:processing"),
        ("remove", 42, "pdf:signed"),
        ("remove", 42, "archive:ready"),
        ("add", 42, "pdf:error"),
    ]


def test_done_tag_always_blocks_processing_even_without_a_required_trigger() -> None:
    assert should_process(["archive:ready"], trigger_tag="archive:ready") is True
    assert should_process(["pdf:signed"], require_trigger_tag=False) is False
