"""Define the write-once archive store protocol shared by all storage backends.

Each revision is stored once under its own key prefix and is never replaced. A revision
is built through a ``RevisionWriter`` (create-only objects in a private staging area) and
becomes visible atomically at ``finalize``. Keys and prefixes are relative POSIX paths.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Protocol

from chronikwerk.failures import PermanentError

__all__ = [
    "ArchiveStore",
    "ArchiveStoreError",
    "ObjectExistsError",
    "RevisionExistsError",
    "RevisionWriter",
    "StoredObject",
    "validate_key",
    "validate_prefix",
]


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Describe one stored object; ``sha256`` is None when it was not computed."""

    key: str
    size: int
    sha256: str | None
    retain_until: datetime | None
    legal_hold: bool


class ArchiveStoreError(PermanentError):
    """Base class for archive store failures that must not be retried."""


class ObjectExistsError(ArchiveStoreError):
    """Create-only was violated: the key exists with different content."""


class RevisionExistsError(ArchiveStoreError):
    """The final revision prefix is already present."""


class RevisionWriter(Protocol):
    """Build one revision. Missing objects raise ``FileNotFoundError`` on read."""

    prefix: str

    def put_new(self, relative_key: str, source: Path, *, retain_until: datetime) -> StoredObject:
        """Create-only copy of ``source``; identical content under the same key is a no-op."""
        ...

    def open(self, relative_key: str) -> BinaryIO:
        """Read back an object this writer stored."""
        ...

    def keys(self) -> list[str]:
        """Return the sorted relative keys stored so far."""
        ...

    def finalize(self, *, retain_until: datetime) -> None:
        """Atomically publish the revision under its final prefix."""
        ...

    def abort(self) -> None:
        """Discard the unfinalized revision."""
        ...


class ArchiveStore(Protocol):
    """Read access to finalized revisions plus creation of new ones."""

    retention_enforced: bool

    def begin_revision(self, prefix: str, *, resume: bool = False) -> RevisionWriter:
        """Start (or with ``resume`` continue) a revision under ``prefix``."""
        ...

    def open(self, key: str) -> BinaryIO:
        """Open a finalized object; ``FileNotFoundError`` if absent."""
        ...

    def head(self, key: str) -> StoredObject:
        """Return metadata for a finalized object without hashing it."""
        ...

    def list(self, prefix: str) -> Iterator[str]:
        """Yield the keys of finalized objects below ``prefix``."""
        ...

    def exists(self, prefix: str) -> bool:
        """Report whether a finalized revision exists at ``prefix``."""
        ...


def _validate_segments(value: str, *, label: str, segments: list[str]) -> None:
    if "\\" in value or "\x00" in value:
        raise ArchiveStoreError(f"{label} must not contain backslashes or NUL")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ArchiveStoreError(f"{label} must not contain empty, '.' or '..' segments")


def validate_key(key: str) -> str:
    """Return ``key`` if it is a safe relative POSIX object key."""
    _validate_segments(key, label="key", segments=key.split("/"))
    return key


def validate_prefix(prefix: str) -> str:
    """Return ``prefix`` if it is a safe relative POSIX prefix ending with '/'."""
    if not prefix.endswith("/"):
        raise ArchiveStoreError("prefix must end with '/'")
    _validate_segments(prefix, label="prefix", segments=prefix[:-1].split("/"))
    return prefix
