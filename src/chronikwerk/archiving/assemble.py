"""Turn a captured source plus its reading copies into a package with a written manifest."""

# Contract: preservation brief (ADRs 0011-0013) section 5.4.

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chronikwerk.archiving.capture import CapturedSource
from chronikwerk.archiving.rendering import ReadingCopies
from chronikwerk.preservation.manifest import (
    FileRole,
    Manifest,
    Producer,
    TicketRef,
    parse_manifest,
)

__all__ = [
    "ACCESSIBLE_COPY_PATH",
    "ARCHIVAL_COPY_PATH",
    "CAPTURE_DESCRIPTION_PATH",
    "AssembledPackage",
    "assemble_package",
    "capture_description_bytes",
]

ARCHIVAL_COPY_PATH = "reading-copy.pdf"
ACCESSIBLE_COPY_PATH = "reading-copy-ua.pdf"
CAPTURE_DESCRIPTION_PATH = "capture.json"
_PDF = "application/pdf"
_JSON = "application/json"
_READING_COPY_SOURCES = ("source/ticket.json", "source/articles.json")


@dataclass(frozen=True, slots=True)
class AssembledPackage:
    """A complete, not yet sealed package: manifest bytes, parsed manifest and build directory."""

    manifest_bytes: bytes
    manifest: Manifest
    build_dir: Path


def _json_default(value: object) -> str:
    """Render datetimes as UTC ISO 8601 with a ``Z`` suffix; refuse anything else."""
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("capture description datetimes must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    raise TypeError(f"capture description value is not JSON serializable: {type(value).__name__}")


def capture_description_bytes(document: Mapping[str, Any]) -> bytes:
    """Serialize a capture description deterministically: sorted keys, indent 2, UTF-8, LF."""
    text = json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False, default=_json_default)
    return (text + "\n").encode("utf-8")


def _captured_facts(
    captured: CapturedSource, *, revision_id: str, producer: Producer
) -> dict[str, Any]:
    """Return the capture-description keys that Chronikwerk derives from the capture itself."""
    return {
        "revision_id": revision_id,
        "producer": {"name": producer.name, "version": producer.version},
        "collection": {
            "started": captured.collection_started,
            "finished": captured.collection_finished,
            "attempts": captured.attempts,
        },
        "inventories": {
            "before": captured.inventory_before,
            "after": captured.inventory_after,
        },
        "record_class": captured.record_class,
        "coverage": dataclasses.asdict(captured.coverage),
        "mail_auth": {
            str(article_id): auth for article_id, auth in sorted(captured.mail_auth.items())
        },
    }


def _capture_document(
    captured: CapturedSource,
    capture_description: Mapping[str, Any],
    *,
    revision_id: str,
    producer: Producer,
) -> dict[str, Any]:
    facts = _captured_facts(captured, revision_id=revision_id, producer=producer)
    overlap = sorted(set(facts) & set(capture_description))
    if overlap:
        raise ValueError(f"capture description must not set derived keys: {', '.join(overlap)}")
    return {**capture_description, **facts}


def _add_reading_copies(captured: CapturedSource, copies: ReadingCopies) -> None:
    builder = captured.builder
    for path, data, role in (
        (ARCHIVAL_COPY_PATH, copies.archival, FileRole.READING_COPY_ARCHIVAL),
        (ACCESSIBLE_COPY_PATH, copies.accessible, FileRole.READING_COPY_ACCESSIBLE),
    ):
        if data is not None:
            builder.add_bytes(
                path, data, role=role, media_type=_PDF, derived_from=_READING_COPY_SOURCES
            )


def assemble_package(
    captured: CapturedSource,
    copies: ReadingCopies,
    *,
    revision_id: str,
    revision_number: int,
    previous_revision_id: str | None,
    policy_id: str,
    captured_at: datetime,
    producer: Producer,
    capture_description: Mapping[str, Any],
) -> AssembledPackage:
    """
    Add the reading copies and ``capture.json`` to the capture's build area, then the manifest.

    ``capture_description`` carries what the capture cannot know (source origin, request ID
    and source, the record-class rule with ``retain_until``, limits in force). Collection
    times, attempts, inventories, record class, coverage, ``mail_auth``, the producer and the
    revision ID are added here; a caller key that collides with one of them is a ``ValueError``.
    """
    document = _capture_document(
        captured, capture_description, revision_id=revision_id, producer=producer
    )
    _add_reading_copies(captured, copies)
    captured.builder.add_bytes(
        CAPTURE_DESCRIPTION_PATH,
        capture_description_bytes(document),
        role=FileRole.CAPTURE_DESCRIPTION,
        media_type=_JSON,
    )
    manifest_bytes = captured.builder.write_manifest(
        revision_id=revision_id,
        revision_number=revision_number,
        previous_revision_id=previous_revision_id,
        ticket=TicketRef(captured.ticket.id, captured.ticket.number),
        captured_at=captured_at,
        record_class=captured.record_class,
        policy_id=policy_id,
        source_digest=captured.source_digest,
        coverage=captured.coverage,
        producer=producer,
    )
    return AssembledPackage(
        manifest_bytes=manifest_bytes,
        manifest=parse_manifest(manifest_bytes),
        build_dir=captured.build_dir,
    )
