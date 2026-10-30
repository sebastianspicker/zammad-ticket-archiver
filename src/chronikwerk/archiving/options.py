"""Immutable runtime values for the archive use case."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArchiveWorkflowOptions:
    """Configure workflow tags and the ticket fields that shape archive paths."""

    trigger_tag: str
    require_trigger_tag: bool
    acknowledge_on_success: bool
    archive_path_field_name: str
    archive_user_mode_field_name: str
    archive_user_field_name: str
