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
