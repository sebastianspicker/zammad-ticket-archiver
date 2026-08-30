"""Immutable runtime values consumed by archive storage and audit writing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ArchiveStorageOptions:
    """Configure path layout and filesystem durability for one archive repository."""

    root: Path
    fsync: bool
    filename_pattern: str


@dataclass(frozen=True, slots=True)
class SigningProvenance:
    """Record non-secret signing evidence in an audit sidecar."""

    enabled: bool
    tsa_used: bool
    certificate_fingerprint: str | None = None
