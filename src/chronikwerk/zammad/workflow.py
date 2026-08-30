"""Project archival workflow state through sequential Zammad tag changes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

TRIGGER_TAG = "pdf:sign"
PROCESSING_TAG = "pdf:processing"
DONE_TAG = "pdf:signed"
ERROR_TAG = "pdf:error"


def should_process(
    tags: Iterable[str] | None,
    *,
    trigger_tag: str = TRIGGER_TAG,
    require_trigger_tag: bool = True,
) -> bool:
    """Decide whether the configured trigger tags permit processing."""
    tag_set = set(tags or [])
    if DONE_TAG in tag_set:
        return False
    if require_trigger_tag:
        return trigger_tag in tag_set
    return True


async def _apply_tag_transition(
    client: Any,
    ticket_id: int,
    remove_tags: Iterable[str],
    add_tag: str,
) -> None:
    for tag in remove_tags:
        await client.remove_tag(ticket_id, tag)
    await client.add_tag(ticket_id, add_tag)


async def apply_processing(
    client: Any,
    ticket_id: int,
    *,
    trigger_tag: str = TRIGGER_TAG,
    force_reprocess: bool = False,
) -> None:
    """Mutate Zammad tags to mark work in progress and clear stale terminal state."""
    remove_tags = (DONE_TAG,) if force_reprocess else ()
    await _apply_tag_transition(
        client,
        ticket_id,
        (*remove_tags, ERROR_TAG, trigger_tag),
        PROCESSING_TAG,
    )


async def apply_done(client: Any, ticket_id: int, *, trigger_tag: str = TRIGGER_TAG) -> None:
    """Mutate Zammad tags to record success and clear trigger or error state."""
    await _apply_tag_transition(
        client,
        ticket_id,
        (PROCESSING_TAG, ERROR_TAG, trigger_tag),
        DONE_TAG,
    )


async def apply_error(
    client: Any,
    ticket_id: int,
    *,
    keep_trigger: bool = True,
    trigger_tag: str = TRIGGER_TAG,
) -> None:
    """Mutate Zammad tags to record failure while optionally preserving the trigger."""
    await client.remove_tag(ticket_id, PROCESSING_TAG)
    await client.remove_tag(ticket_id, DONE_TAG)
    if keep_trigger:
        await client.add_tag(ticket_id, trigger_tag)
    else:
        await client.remove_tag(ticket_id, trigger_tag)
    await client.add_tag(ticket_id, ERROR_TAG)
