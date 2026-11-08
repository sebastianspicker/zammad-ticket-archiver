"""Project archival workflow state through sequential Zammad tag changes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

PROCESSING_TAG = "pdf:processing"
DONE_TAG = "pdf:signed"
ERROR_TAG = "pdf:error"


class TagClient(Protocol):
    """Mutate the tags of one Zammad ticket."""

    async def add_tag(self, ticket_id: int, tag: str) -> None:
        """Add one tag to the ticket."""

    async def remove_tag(self, ticket_id: int, tag: str) -> None:
        """Remove one tag from the ticket."""


async def _apply_tag_transition(
    client: TagClient,
    ticket_id: int,
    remove_tags: Iterable[str],
    add_tag: str,
) -> None:
    for tag in remove_tags:
        await client.remove_tag(ticket_id, tag)
    await client.add_tag(ticket_id, add_tag)


async def apply_processing(
    client: TagClient,
    ticket_id: int,
    *,
    trigger_tag: str,
    force_reprocess: bool = False,
) -> None:
    """Mutate Zammad tags to mark work in progress and clear stale terminal state.

    ``pdf:processing`` is added first, so a crash before the removals never leaves the
    ticket with neither the processing tag nor its trigger or error tag.
    """
    await client.add_tag(ticket_id, PROCESSING_TAG)
    remove_tags = (DONE_TAG,) if force_reprocess else ()
    for tag in (*remove_tags, ERROR_TAG, trigger_tag):
        await client.remove_tag(ticket_id, tag)


async def apply_done(client: TagClient, ticket_id: int) -> None:
    """Mutate Zammad tags to record success and clear processing or error state.

    The trigger tag is left alone: ``apply_processing`` already removed it, so a trigger tag
    present now was set again during the run and belongs to the next, coalesced request.
    """
    await _apply_tag_transition(client, ticket_id, (PROCESSING_TAG, ERROR_TAG), DONE_TAG)


async def apply_error(
    client: TagClient,
    ticket_id: int,
    *,
    keep_trigger: bool = True,
    trigger_tag: str,
) -> None:
    """Mutate Zammad tags to record failure while optionally preserving the trigger."""
    await client.remove_tag(ticket_id, PROCESSING_TAG)
    await client.remove_tag(ticket_id, DONE_TAG)
    if keep_trigger:
        await client.add_tag(ticket_id, trigger_tag)
    else:
        await client.remove_tag(ticket_id, trigger_tag)
    await client.add_tag(ticket_id, ERROR_TAG)
