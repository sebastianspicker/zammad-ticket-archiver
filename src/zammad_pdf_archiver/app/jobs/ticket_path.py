from typing import Any


def require_nonempty(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    out = value.strip()
    if not out:
        raise ValueError(f"{field} must be non-empty")
    return out


def determine_username(
    *,
    ticket: Any,
    payload: dict[str, Any],
    custom_fields: dict[str, Any],
    mode_field_name: str,
    archive_user_field_name: str = "archive_user",
) -> str:
    raw_mode = custom_fields.get(mode_field_name)
    mode = str(raw_mode).strip() if raw_mode is not None else "owner"

    if mode == "owner":
        owner = getattr(ticket, "owner", None)
        return require_nonempty(getattr(owner, "login", None), field="ticket.owner.login")

    if mode == "current_agent":
        user = payload.get("user")
        if isinstance(user, dict):
            login = user.get("login")
            if isinstance(login, str) and login.strip():
                return login.strip()

        updated_by = getattr(ticket, "updated_by", None)
        return require_nonempty(
            getattr(updated_by, "login", None),
            field="ticket.updated_by.login",
        )

    if mode == "fixed":
        return require_nonempty(
            custom_fields.get(archive_user_field_name),
            field=f"custom_fields.{archive_user_field_name}",
        )

    raise ValueError(f"unsupported archive_user_mode: {mode!r}")


def parse_archive_path_segments(value: Any) -> list[str]:
    if value is None:
        raise ValueError("custom_fields.archive_path is missing")

    if isinstance(value, str):
        raw_parts = [p.strip() for p in value.split(">")]
        parts = [p for p in raw_parts if p]
    elif isinstance(value, list):
        parts = []
        for idx, item in enumerate(value):
            if not isinstance(item, str):
                raise ValueError(f"custom_fields.archive_path[{idx}] must be a string")
            item = item.strip()
            if item:
                parts.append(item)
    else:
        raise ValueError("custom_fields.archive_path must be a string or list of strings")

    if not parts:
        raise ValueError(
            "custom_fields.archive_path must not be empty after sanitization "
            "(all segments were empty or whitespace-only)"
        )

    return parts
