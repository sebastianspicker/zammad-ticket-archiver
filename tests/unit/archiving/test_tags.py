"""Test the ordered public tag projections used by the archive workflow."""

from __future__ import annotations

import asyncio

import pytest

from chronikwerk.archiving.tags import apply_done, apply_error, apply_processing


class _TagClient:
    """Record sequential tag mutations without contacting Zammad."""

    def __init__(self) -> None:
        self.events: list[tuple[str, int, str]] = []

    async def remove_tag(self, ticket_id: int, tag: str) -> None:
        self.events.append(("remove", ticket_id, tag))

    async def add_tag(self, ticket_id: int, tag: str) -> None:
        self.events.append(("add", ticket_id, tag))


def test_processing_tag_is_added_before_terminal_and_trigger_tags_are_cleared() -> None:
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
        ("add", 42, "pdf:processing"),
        ("remove", 42, "pdf:signed"),
        ("remove", 42, "pdf:error"),
        ("remove", 42, "archive:ready"),
    ]


class _CrashingTagClient(_TagClient):
    """Fail every removal, like a process that dies after the first tag change."""

    async def remove_tag(self, ticket_id: int, tag: str) -> None:
        raise RuntimeError(f"crashed before removing {tag}")


def test_failed_removal_leaves_the_processing_tag_already_set() -> None:
    client = _CrashingTagClient()

    with pytest.raises(RuntimeError):
        asyncio.run(apply_processing(client, 42, trigger_tag="archive:ready"))

    assert client.events == [("add", 42, "pdf:processing")]


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
