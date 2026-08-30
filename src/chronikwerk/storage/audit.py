"""Define structured audit records written alongside archived tickets."""

# pylint: disable=import-outside-toplevel
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from chronikwerk._version import __version__
from chronikwerk.storage.options import SigningProvenance
from chronikwerk.timestamps import format_timestamp_utc

_UNSIGNED_PROVENANCE = SigningProvenance(False, False)


@dataclass(frozen=True)
class AuditRecordInput:
    """Capture the values required to create an archival audit record."""

    ticket_id: int
    ticket_number: str
    title: str | None
    created_at: datetime
    storage_path: str
    sha256: str
    articles_total: int | None = None
    articles_included: int | None = None
    articles_omitted: int = 0


def _signing_evidence(
    provenance: SigningProvenance,
) -> dict[str, Any]:
    signing: dict[str, Any] = {"enabled": provenance.enabled, "tsa_used": provenance.tsa_used}
    if provenance.enabled and provenance.certificate_fingerprint:
        signing["cert_fingerprint"] = provenance.certificate_fingerprint
    return signing


def _article_coverage(record: AuditRecordInput) -> dict[str, int | bool | None]:
    return {
        "total": record.articles_total,
        "included": record.articles_included,
        "omitted": record.articles_omitted,
        "complete": record.articles_omitted == 0,
    }


def build_audit_record(
    record: AuditRecordInput,
    *,
    signing_provenance: SigningProvenance = _UNSIGNED_PROVENANCE,
    service_name: str = "chronikwerk",
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a JSON-serialisable audit record for a successfully archived ticket."""
    signing = _signing_evidence(signing_provenance)

    service: dict[str, Any] = {
        "name": service_name,
        "version": __version__,
        "python": sys.version.split(" ", 1)[0],
    }

    out: dict[str, Any] = {
        "ticket_id": int(record.ticket_id),
        "ticket_number": str(record.ticket_number),
        "title": (record.title or "").strip(),
        "created_at": format_timestamp_utc(record.created_at),
        "storage_path": str(record.storage_path),
        "sha256": str(record.sha256),
        "signing": signing,
        "service": service,
    }
    if record.articles_total is not None:
        out["article_coverage"] = _article_coverage(record)
    if attachments:
        out["attachments"] = attachments
    return out
