"""Immutable runtime values for the archive use case."""

from __future__ import annotations

from dataclasses import dataclass

from chronikwerk.configuration.zammad import ZammadConnection
from chronikwerk.documents.options import DocumentOptions
from chronikwerk.storage.options import ArchiveStorageOptions


@dataclass(frozen=True, slots=True)
class ArchiveWorkflowOptions:
    """Configure workflow tags, archive fields, and process-local deduplication."""

    trigger_tag: str
    require_trigger_tag: bool
    acknowledge_on_success: bool
    delivery_id_ttl_seconds: int
    archive_path_field_name: str
    archive_user_mode_field_name: str
    archive_user_field_name: str


@dataclass(frozen=True, slots=True)
class ArchiveRuntimeOptions:
    """All narrow runtime dependencies required by the archive use case."""

    connection: ZammadConnection
    workflow: ArchiveWorkflowOptions
    documents: DocumentOptions
    storage: ArchiveStorageOptions
