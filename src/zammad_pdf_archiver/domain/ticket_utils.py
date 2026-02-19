"""Shared helpers for ticket data (e.g. custom fields)."""

from __future__ import annotations

from typing import Any


def ticket_custom_fields(ticket: Any) -> dict[str, Any]:
    """Extract custom_fields from ticket.preferences, or return empty dict."""
    prefs = getattr(ticket, "preferences", None)
    if prefs is None:
        return {}
    fields = getattr(prefs, "custom_fields", None)
    if isinstance(fields, dict):
        return fields
    return {}
