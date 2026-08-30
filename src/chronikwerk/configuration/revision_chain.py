"""Managed configuration revision payload and chain helpers."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeGuard

from chronikwerk.configuration.errors import (
    ManagedConfigError,
    _MissingManagedFile,
)

_REVISION_RE = re.compile(r"^[a-f0-9]{64}$")

OverlayValidator = Callable[[dict[str, Any]], None]
RevisionReader = Callable[[Path], dict[str, Any]]


def revision_for(overlay: dict[str, Any]) -> str:
    """Derive the content-addressed revision identifier for a configuration payload."""
    payload = json.dumps(overlay, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_revision_identifier(value: object) -> TypeGuard[str]:
    """Check whether a value is a syntactically valid revision identifier."""
    return isinstance(value, str) and _REVISION_RE.fullmatch(value) is not None


def parse_current_payload(
    payload: bytes | None,
    *,
    validate_overlay: OverlayValidator,
) -> tuple[dict[str, Any], str]:
    """Parse and validate the current managed configuration payload."""
    if payload is None:
        return {}, revision_for({})

    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict):
        raise ManagedConfigError("Managed configuration must be a JSON object")
    if set(data) != {"revision", "overlay"}:
        validate_overlay(data)
        return data, revision_for(data)

    revision = data.get("revision")
    overlay = data.get("overlay")
    if not is_revision_identifier(revision):
        raise ManagedConfigError("Managed configuration revision is invalid")
    if not isinstance(overlay, dict):
        raise ManagedConfigError("Managed configuration overlay is invalid")
    validate_overlay(overlay)
    return overlay, revision


def parse_revision_payload(
    payload: bytes,
    *,
    expected_revision: str,
    validate_overlay: OverlayValidator,
) -> dict[str, Any]:
    """Parse a retained revision while checking its stored integrity."""
    data = json.loads(payload.decode("utf-8"))
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("overlay"), dict)
        or not isinstance(data.get("metadata"), dict)
    ):
        raise ManagedConfigError("Invalid revision file")

    metadata = data["metadata"]
    revision = metadata.get("revision")
    previous = metadata.get("previous_revision")
    if (
        not is_revision_identifier(revision)
        or not is_revision_identifier(previous)
        or expected_revision != revision
    ):
        raise ManagedConfigError("Invalid revision metadata")
    validate_overlay(data["overlay"])
    return data


def _read_revision_chain_entry(
    revision: str,
    *,
    revisions_dir: Path,
    read_revision: RevisionReader,
) -> tuple[Path, dict[str, Any]] | None:
    """Read one revision, allowing the absent empty-root revision."""
    path = revisions_dir / f"{revision}.json"
    try:
        return path, read_revision(path)
    except _MissingManagedFile:
        if revision == revision_for({}):
            return None
        raise ManagedConfigError(
            f"Revision chain references missing revision: {revision}"
        ) from None


def build_revision_chain(
    *,
    current_revision: str,
    revisions_dir: Path,
    read_revision: RevisionReader,
    max_entries: int | None = None,
) -> list[tuple[Path, dict[str, Any]]]:
    """Build the bounded lineage used for managed configuration history."""
    chain: list[tuple[Path, dict[str, Any]]] = []
    revision = current_revision
    seen: set[str] = set()
    while revision not in seen:
        if max_entries is not None and len(chain) >= max_entries:
            break
        seen.add(revision)
        entry = _read_revision_chain_entry(
            revision,
            revisions_dir=revisions_dir,
            read_revision=read_revision,
        )
        if entry is None:
            break
        path, data = entry
        chain.append((path, data))
        revision = str(data["metadata"]["previous_revision"])
    return chain


def retained_revision_names(
    chain: list[tuple[Path, dict[str, Any]]],
    keep_revisions: int,
) -> set[str]:
    """Return retained revision filenames in newest-first restore order."""
    return {path.name for path, _data in chain[:keep_revisions]}
