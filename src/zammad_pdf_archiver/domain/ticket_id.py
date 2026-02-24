from __future__ import annotations

from typing import Any


def coerce_ticket_id(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None

    if isinstance(value, int):
        return value if value > 0 else None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.startswith("+"):
            text = text[1:]
        if not text.isdigit():
            return None
        ticket_id = int(text)
        return ticket_id if ticket_id > 0 else None

    return None


def extract_ticket_id(payload: dict[str, Any]) -> int | None:
    """
    Extract and coerce ticket ID from a webhook payload (Bug #P1-4).
    Checks ticket_id first, then ticket.id.
    """
    # Prefer top-level ticket_id (explicit).
    tid = coerce_ticket_id(payload.get("ticket_id"))
    if tid is not None:
        return tid

    # Fallback to nested ticket object.
    ticket = payload.get("ticket")
    if isinstance(ticket, dict):
        return coerce_ticket_id(ticket.get("id"))

    # Last resort: try coercive access on 'ticket' if it's not a dict but a scalar id
    return coerce_ticket_id(ticket)
