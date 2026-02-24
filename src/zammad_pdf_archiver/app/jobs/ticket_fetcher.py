"""Ticket fetching operations - handles all Zammad data retrieval.

This module provides a clean interface for fetching ticket-related data
from Zammad, abstracting away the client details.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from zammad_pdf_archiver.adapters.zammad.models import TagList, Ticket

if TYPE_CHECKING:
    from zammad_pdf_archiver.adapters.zammad.client import AsyncZammadClient


@dataclass(frozen=True)
class TicketData:
    """Container for all fetched ticket data."""
    ticket: Ticket
    tags: TagList
    ticket_id: int


async def fetch_ticket_data(
    client: AsyncZammadClient,
    ticket_id: int,
) -> TicketData:
    """Fetch all required ticket data from Zammad.
    
    Args:
        client: Zammad API client
        ticket_id: Ticket ID to fetch
        
    Returns:
        TicketData containing ticket, tags, and ID
    """
    ticket = await client.get_ticket(ticket_id)
    tags = await client.list_tags(ticket_id)
    
    return TicketData(
        ticket=ticket,
        tags=tags,
        ticket_id=ticket_id,
    )
