"""In-memory archive store with object-lock style retention for tests."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from chronikwerk.storage.archive_store import (
    ArchiveStoreError,
    ObjectExistsError,
    RevisionExistsError,
    StoredObject,
    validate_key,
    validate_prefix,
)


@dataclass(frozen=True, slots=True)
class _Entry:
    """One stored object with its retention state."""

    data: bytes
    retain_until: datetime
    legal_hold: bool = False

    def describe(self, key: str) -> StoredObject:
        return StoredObject(
            key=key,
            size=len(self.data),
            sha256=hashlib.sha256(self.data).hexdigest(),
            retain_until=self.retain_until,
            legal_hold=self.legal_hold,
        )


class FakeRevisionWriter:
    """Create-only writer that publishes its objects at ``finalize``."""

    def __init__(
        self,
        store: FakeArchiveStore,
        prefix: str,
        staged: dict[str, _Entry],
        *,
        final: bool,
    ) -> None:
        self.prefix = prefix
        self._store = store
        self._staged = staged
        self._final = final
        self._written: set[str] = set()

    def put_new(self, relative_key: str, source: Path, *, retain_until: datetime) -> StoredObject:
        key = validate_key(relative_key)
        data = source.read_bytes()
        existing = self._staged.get(key)
        if existing is None and self._final:
            raise RevisionExistsError(f"revision {self.prefix} is already final")
        if existing is not None and existing.data != data:
            if self._final or key in self._written:
                raise ObjectExistsError(f"object {self.prefix}{key} exists with different content")
            existing = None  # a staged leftover from an earlier writer is replaced
        if existing is None:
            existing = _Entry(data, retain_until)
            self._staged[key] = existing
        self._written.add(key)
        return existing.describe(f"{self.prefix}{key}")

    def open(self, relative_key: str) -> BinaryIO:
        try:
            return io.BytesIO(self._staged[validate_key(relative_key)].data)
        except KeyError:
            raise FileNotFoundError(relative_key) from None

    def keys(self) -> list[str]:
        return sorted(self._staged)

    def finalize(self, *, retain_until: datetime) -> None:
        if self._final:
            return
        if not self._staged:
            raise ArchiveStoreError(f"revision {self.prefix} has no objects; refusing to publish")
        if self.prefix in self._store.revisions or self._store.has_objects(self.prefix):
            raise RevisionExistsError(f"revision {self.prefix} already exists")
        for key, entry in self._staged.items():
            extended = replace(entry, retain_until=max(entry.retain_until, retain_until))
            self._store.objects[f"{self.prefix}{key}"] = extended
        self._store.revisions.add(self.prefix)
        self._store.staging.pop(self.prefix, None)
        self._final = True

    def abort(self) -> None:
        if not self._final:
            self._store.staging.pop(self.prefix, None)
            self._staged.clear()


class FakeArchiveStore:
    """Write-once store whose objects cannot shrink in retention or be deleted early."""

    def __init__(self) -> None:
        self.retention_enforced = True
        self.objects: dict[str, _Entry] = {}
        self.revisions: set[str] = set()
        self.staging: dict[str, dict[str, _Entry]] = {}

    def has_objects(self, prefix: str) -> bool:
        return any(key.startswith(prefix) for key in self.objects)

    def begin_revision(self, prefix: str, *, resume: bool = False) -> FakeRevisionWriter:
        validate_prefix(prefix)
        if self.exists(prefix):
            if not resume:
                raise RevisionExistsError(f"revision {prefix} already exists")
            visible = {
                key[len(prefix) :]: entry
                for key, entry in self.objects.items()
                if key.startswith(prefix)
            }
            return FakeRevisionWriter(self, prefix, visible, final=True)
        if prefix in self.staging and not resume:
            raise ArchiveStoreError(f"revision {prefix} is already being staged")
        staged = self.staging.setdefault(prefix, {})
        return FakeRevisionWriter(self, prefix, staged, final=False)

    def open(self, key: str) -> BinaryIO:
        try:
            return io.BytesIO(self.objects[validate_key(key)].data)
        except KeyError:
            raise FileNotFoundError(key) from None

    def head(self, key: str) -> StoredObject:
        validate_key(key)
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key].describe(key)

    def list(self, prefix: str) -> Iterator[str]:
        validate_prefix(prefix)
        return iter(sorted(key for key in self.objects if key.startswith(prefix)))

    def exists(self, prefix: str) -> bool:
        validate_prefix(prefix)
        return prefix in self.revisions or self.has_objects(prefix)

    def extend_retention(self, key: str, retain_until: datetime) -> None:
        """Move the retain-until date later; shortening is refused."""
        entry = self.objects[key]
        if retain_until < entry.retain_until:
            raise ArchiveStoreError("retention cannot be shortened")
        self.objects[key] = replace(entry, retain_until=retain_until)

    def set_legal_hold(self, key: str, on: bool) -> None:
        self.objects[key] = replace(self.objects[key], legal_hold=on)

    def delete(self, key: str, *, now: datetime) -> None:
        """Delete an object only once its retention has passed and no hold is set."""
        entry = self.objects[key]
        if entry.legal_hold:
            raise ArchiveStoreError("object is under legal hold")
        if now < entry.retain_until:
            raise ArchiveStoreError("object is still under retention")
        del self.objects[key]
