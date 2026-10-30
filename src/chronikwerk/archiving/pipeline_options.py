"""Runtime values of the preservation pipeline and the retention facts derived from them."""

# Contract: preservation brief (ADRs 0011-0013) sections 5.3, 5.4 and 5.11.

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from chronikwerk.archiving.capture import CaptureOptions
from chronikwerk.archiving.options import ArchiveWorkflowOptions
from chronikwerk.documents.options import DocumentOptions
from chronikwerk.preservation.manifest import Producer
from chronikwerk.preservation.seal import SealOptions
from chronikwerk.timestamps import add_iso8601_date_duration

__all__ = [
    "READING_COPY_VARIANTS",
    "PreservationOptions",
    "ReadingCopyVariant",
    "RetentionRule",
    "compute_retain_until",
    "rule_document",
]

ReadingCopyVariant = Literal["pdf/a-2u", "pdf/ua-1"]
READING_COPY_VARIANTS: tuple[ReadingCopyVariant, ...] = ("pdf/a-2u", "pdf/ua-1")


class RetentionRule(Protocol):
    """One record-class rule of the retention policy (``configuration.retention``)."""

    @property
    def name(self) -> str:
        """Return the record class name."""
        ...

    @property
    def retention(self) -> str:
        """Return the ISO 8601 date duration, such as ``P6Y``."""
        ...

    @property
    def trigger(self) -> Literal["capture", "case_closed"]:
        """Return the event the retention period starts from."""
        ...

    @property
    def readers(self) -> tuple[str, ...]:
        """Return the groups documented as readers of the class prefix."""
        ...


@dataclass(frozen=True, slots=True)
class PreservationOptions:
    """Everything the preservation pipeline needs besides its services."""

    workflow: ArchiveWorkflowOptions
    documents: DocumentOptions
    capture: CaptureOptions
    seal: SealOptions
    trust_anchors: tuple[Any, ...]
    reading_copies: tuple[str, ...]
    retention_rules: Mapping[str, RetentionRule]
    temp_dir: Path
    producer: Producer
    source_origin: str

    def __post_init__(self) -> None:
        """Reject reading-copy variants the renderer does not know and an empty policy."""
        if not self.reading_copies:
            raise ValueError("reading_copies must name at least one variant")
        unknown = sorted(set(self.reading_copies) - set(READING_COPY_VARIANTS))
        if unknown:
            raise ValueError(f"unknown reading copy variants: {', '.join(unknown)}")
        if not self.retention_rules:
            raise ValueError("retention_rules must hold at least one record class")

    @property
    def variants(self) -> tuple[ReadingCopyVariant, ...]:
        """Return the reading-copy variants typed for the renderer."""
        return cast(tuple[ReadingCopyVariant, ...], self.reading_copies)

    def capture_options(self) -> CaptureOptions:
        """Return the capture options with the record classes the retention policy knows."""
        return dataclasses.replace(
            self.capture, known_record_classes=frozenset(self.retention_rules)
        )


def rule_document(rule: RetentionRule) -> dict[str, Any]:
    """Return the record-class rule as the JSON object hashed into the policy ID."""
    return {
        "name": rule.name,
        "retention": rule.retention,
        "trigger": rule.trigger,
        "readers": list(rule.readers),
    }


def _closed_at(ticket_raw: Mapping[str, Any]) -> datetime | None:
    """Return the ticket's ``close_at`` as an aware UTC datetime, or ``None`` when unset."""
    value = ticket_raw.get("close_at")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("ticket close_at must be an ISO 8601 string")
    closed = datetime.fromisoformat(value)
    return closed.replace(tzinfo=UTC) if closed.tzinfo is None else closed.astimezone(UTC)


def compute_retain_until(
    rule: RetentionRule, *, captured_at: datetime, ticket_raw: Mapping[str, Any]
) -> datetime:
    """Return trigger time plus retention (brief section 5.11).

    ``capture`` starts at ``captured_at``; ``case_closed`` starts at the ticket's ``close_at``,
    or at ``captured_at`` while the ticket is still open (reconciliation extends it later).
    """
    start = captured_at
    if rule.trigger == "case_closed":
        start = _closed_at(ticket_raw) or captured_at
    return add_iso8601_date_duration(start, rule.retention)
