from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

TRIGGER_TAG = "pdf:sign"
PROCESSING_TAG = "pdf:processing"
DONE_TAG = "pdf:signed"
ERROR_TAG = "pdf:error"


class TicketTagger(Protocol):
    async def add_tag(self, ticket_id: int, tag: str) -> None: ...
    async def remove_tag(self, ticket_id: int, tag: str) -> None: ...


def should_process(
    tags: Iterable[str] | None,
    *,
    trigger_tag: str = TRIGGER_TAG,
    require_trigger_tag: bool = True,
) -> bool:
    tag_set = set(tags or [])
    if DONE_TAG in tag_set:
        return False
    if require_trigger_tag:
        return trigger_tag in tag_set
    return True


async def apply_processing(client: TicketTagger, ticket_id: int, *, trigger_tag: str = TRIGGER_TAG) -> None:
    # Deterministic, idempotent transition: any state -> processing
    await client.remove_tag(ticket_id, DONE_TAG)
    await client.remove_tag(ticket_id, ERROR_TAG)
    await client.remove_tag(ticket_id, trigger_tag)
    await client.add_tag(ticket_id, PROCESSING_TAG)


async def apply_done(client: TicketTagger, ticket_id: int, *, trigger_tag: str = TRIGGER_TAG) -> None:
    # Deterministic, idempotent transition: any state -> done
    await client.remove_tag(ticket_id, PROCESSING_TAG)
    await client.remove_tag(ticket_id, ERROR_TAG)
    await client.remove_tag(ticket_id, trigger_tag)
    await client.add_tag(ticket_id, DONE_TAG)


async def apply_error(
    client: TicketTagger,
    ticket_id: int,
    *,
    keep_trigger: bool = True,
    trigger_tag: str = TRIGGER_TAG,
) -> None:
    # Deterministic, idempotent transition: any state -> error
    await client.remove_tag(ticket_id, PROCESSING_TAG)
    await client.remove_tag(ticket_id, DONE_TAG)
    if keep_trigger:
        await client.add_tag(ticket_id, trigger_tag)
    else:
        await client.remove_tag(ticket_id, trigger_tag)
    await client.add_tag(ticket_id, ERROR_TAG)
