"""Resolve the archive user and archive path of a ticket under the path policy."""

# Policy: docs/04-path-policy.md. The sanitized segments feed the revision key prefix
# (preservation brief (ADRs 0011-0013) section 5.4).

from __future__ import annotations

from pathlib import Path
from typing import Any

from chronikwerk.archiving.options import ArchiveWorkflowOptions
from chronikwerk.archiving.path import determine_username, parse_archive_path_segments
from chronikwerk.storage.layout import build_target_dir
from chronikwerk.zammad.dto import Ticket

__all__ = [
    "archive_custom_fields",
    "archive_user_and_segments",
    "resolve_archive_location",
    "snapshot_custom_fields",
]

# build_target_dir validates, sanitizes and confines below a root; the policy itself does not
# depend on the root, so a fixed one yields the sanitized segments.
_POLICY_ROOT = Path("/")


def archive_custom_fields(ticket: Ticket, workflow: ArchiveWorkflowOptions) -> dict[str, Any]:
    """Collect the configured archive custom attributes that are set on the ticket."""
    names = (
        workflow.archive_path_field_name,
        workflow.archive_user_mode_field_name,
        workflow.archive_user_field_name,
    )
    return {name: value for name in names if (value := ticket.attribute(name)) is not None}


def snapshot_custom_fields(ticket: Ticket, workflow: ArchiveWorkflowOptions) -> dict[str, Any]:
    """Key the configured archive attributes by the names the PDF template reads."""
    canonical = (
        ("archive_path", workflow.archive_path_field_name),
        ("archive_user_mode", workflow.archive_user_mode_field_name),
        ("archive_user", workflow.archive_user_field_name),
    )
    return {key: value for key, name in canonical if (value := ticket.attribute(name)) is not None}


def archive_user_and_segments(
    workflow: ArchiveWorkflowOptions,
    *,
    ticket: Ticket,
    payload: dict[str, Any],
    service_login: str | None = None,
) -> tuple[str, list[str]]:
    """Return the raw archive user and parsed archive path segments, before sanitizing."""
    custom_fields = archive_custom_fields(ticket, workflow)
    username = determine_username(
        ticket=ticket,
        payload=payload,
        custom_fields=custom_fields,
        mode_field_name=workflow.archive_user_mode_field_name,
        archive_user_field_name=workflow.archive_user_field_name,
        service_login=service_login,
    )
    segments = parse_archive_path_segments(custom_fields.get(workflow.archive_path_field_name))
    return username, segments


def resolve_archive_location(
    workflow: ArchiveWorkflowOptions,
    *,
    ticket: Ticket,
    payload: dict[str, Any],
    service_login: str | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Return ``(archive_user, archive_path_segments)`` sanitized by the path policy.

    Raises ``ValueError`` (a permanent failure) for missing or invalid ticket fields.
    """
    username, segments = archive_user_and_segments(
        workflow, ticket=ticket, payload=payload, service_login=service_login
    )
    sanitized = build_target_dir(_POLICY_ROOT, username, segments).relative_to(_POLICY_ROOT)
    archive_user, *archive_path = sanitized.parts
    return archive_user, tuple(archive_path)
