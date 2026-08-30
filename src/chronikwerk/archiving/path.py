"""Resolve archive ownership and path metadata from ticket configuration."""

from typing import Any


def require_nonempty(value: Any, *, field: str) -> str:
    """Return the stripped string value or raise ValueError if it is empty or not a string."""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    out = value.strip()
    if not out:
        raise ValueError(f"{field} must be non-empty")
    return out


def _owner_login(ticket: Any) -> str:
    login = ticket.owner.login if ticket.owner is not None else None
    return require_nonempty(login, field="ticket.owner.login")


def _current_agent_login(ticket: Any, payload: dict[str, Any]) -> str:
    user = payload.get("user")
    if isinstance(user, dict):
        login = user.get("login")
        if isinstance(login, str) and login.strip():
            return login.strip()

    login = ticket.updated_by.login if ticket.updated_by is not None else None
    return require_nonempty(login, field="ticket.updated_by.login")


def _fixed_login(custom_fields: dict[str, Any], archive_user_field_name: str) -> str:
    return require_nonempty(
        custom_fields.get(archive_user_field_name),
        field=f"custom_fields.{archive_user_field_name}",
    )


def determine_username(
    *,
    ticket: Any,
    payload: dict[str, Any],
    custom_fields: dict[str, Any],
    mode_field_name: str,
    archive_user_field_name: str = "archive_user",
) -> str:
    """Resolve the archive username from ticket data based on the configured mode field."""
    raw_mode = custom_fields.get(mode_field_name)
    mode = str(raw_mode).strip() if raw_mode is not None else "owner"

    if mode == "owner":
        return _owner_login(ticket)

    if mode == "current_agent":
        return _current_agent_login(ticket, payload)

    if mode == "fixed":
        return _fixed_login(custom_fields, archive_user_field_name)

    raise ValueError(f"unsupported archive_user_mode: {mode!r}")


def _parse_archive_path_string(value: str) -> list[str]:
    return [part for part in (p.strip() for p in value.split(">")) if part]


def _parse_archive_path_list(value: list[Any]) -> list[str]:
    parts: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"custom_fields.archive_path[{idx}] must be a string")
        item = item.strip()
        if item:
            parts.append(item)
    return parts


def parse_archive_path_segments(value: Any) -> list[str]:
    """Parse and validate archive_path into a non-empty list of non-empty path segments."""
    if value is None:
        raise ValueError("custom_fields.archive_path is missing")

    if isinstance(value, str):
        parts = _parse_archive_path_string(value)
    elif isinstance(value, list):
        parts = _parse_archive_path_list(value)
    else:
        raise ValueError("custom_fields.archive_path must be a string or list of strings")

    if not parts:
        raise ValueError(
            "custom_fields.archive_path must not be empty after sanitization "
            "(all segments were empty or whitespace-only)"
        )

    return parts
