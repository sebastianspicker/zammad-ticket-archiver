"""Build a package in a local directory and verify its files against the manifest."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from chronikwerk.failures import PermanentError
from chronikwerk.preservation.manifest import (
    EVIDENCE_PREFIX,
    MANIFEST_NAME,
    PACKAGE_FORMAT,
    SEAL_NAME,
    TOKEN_NAME,
    Coverage,
    FileRole,
    Manifest,
    ManifestFile,
    Producer,
    TicketRef,
    hash_file,
    is_manifest_listed,
    parse_manifest,
    serialize_manifest,
    validate_package_path,
    validate_role_path,
)
from chronikwerk.preservation.revision import PackageLayoutError

__all__ = [
    "FileProblem",
    "PackageBuilder",
    "PackageTooLargeError",
    "sanitize_filename",
    "verify_package_files",
]

_MAX_FILENAME_CHARS = 120
_COPY_CHUNK_BYTES = 1024 * 1024
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_UNDERSCORE_RUNS = re.compile(r"_+")
_RESERVED_NAMES = frozenset({MANIFEST_NAME, SEAL_NAME, TOKEN_NAME})
# Windows device names stay reserved with any extension (``nul.txt``), in any case.
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"{device}{number}" for device in ("COM", "LPT") for number in range(1, 10)}
)


class PackageTooLargeError(PermanentError):
    """The running package size exceeds the configured limit."""

    code = "package_too_large"


def sanitize_filename(name: str) -> str:
    """Reduce an attachment name to ``[A-Za-z0-9._-]`` and at most 120 characters.

    Trailing dots and spaces are dropped and Windows device names (``CON``, ``nul.txt``,
    ``COM1``) get a ``_`` prefix, so the name means the same file on every platform. The
    result never starts with ``.``: a leading dot is kept behind a ``_`` prefix, so the name
    is neither hidden nor a ``.``/``..`` path segment. An empty result becomes ``file``.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    cleaned = _UNDERSCORE_RUNS.sub("_", _UNSAFE_FILENAME_CHARS.sub("_", stripped.rstrip(". ")))
    if cleaned.startswith(".") or cleaned.split(".", 1)[0].upper() in _WINDOWS_DEVICE_NAMES:
        cleaned = "_" + cleaned
    if len(cleaned) > _MAX_FILENAME_CHARS:
        stem, dot, extension = cleaned.rpartition(".")
        if dot and 0 < len(extension) < _MAX_FILENAME_CHARS:
            cleaned = stem[: _MAX_FILENAME_CHARS - len(extension) - 1] + "." + extension
        else:
            cleaned = cleaned[:_MAX_FILENAME_CHARS]
    # Truncation may end the name in a dot again; the ``_`` prefix keeps it non-empty.
    return cleaned.rstrip(".") or "file"


class PackageBuilder:
    """Collect package files in an empty local directory and write the manifest."""

    def __init__(self, build_dir: Path, *, max_package_bytes: int | None = None) -> None:
        """Bind the builder to an existing, empty directory."""
        if not build_dir.is_dir() or build_dir.is_symlink():
            raise PackageLayoutError(f"build directory does not exist: {build_dir}")
        if any(build_dir.iterdir()):
            raise PackageLayoutError(f"build directory is not empty: {build_dir}")
        self._root = build_dir
        self._max_bytes = max_package_bytes
        self._files: dict[str, ManifestFile] = {}
        self._paths: set[str] = set()
        self._total = 0

    @property
    def total_bytes(self) -> int:
        """Bytes added so far, including evidence files."""
        return self._total

    def files(self) -> tuple[ManifestFile, ...]:
        """Return the listed files sorted by path."""
        return tuple(self._files[path] for path in sorted(self._files))

    def add_bytes(
        self,
        path: str,
        data: bytes,
        *,
        role: FileRole,
        media_type: str,
        derived_from: Sequence[str] = (),
    ) -> ManifestFile:
        """Write ``data`` to ``path`` and list it in the manifest."""
        validate_role_path(path, role)
        target = self._claim(path, len(data), listed=True)
        target.write_bytes(data)
        return self._register(
            path, role, len(data), hashlib.sha256(data).hexdigest(), media_type, derived_from
        )

    def add_file(
        self,
        path: str,
        source: Path,
        *,
        role: FileRole,
        media_type: str,
        derived_from: Sequence[str] = (),
        move: bool = False,
    ) -> ManifestFile:
        """Copy (or move) ``source`` to ``path`` and list it in the manifest.

        The registered size and digest are those of the bytes that landed in the package; a
        source whose size changed since it was measured is refused.
        """
        if source.is_symlink() or not source.is_file():
            raise PackageLayoutError(f"source is not a regular file: {source}")
        validate_role_path(path, role)
        expected = source.stat().st_size
        target = self._claim(path, expected, listed=True)
        if move:
            shutil.move(source, target)
            size, sha256 = hash_file(target)
        else:
            size, sha256 = _copy_and_hash(source, target)
        if size != expected:
            if not move:
                target.unlink(missing_ok=True)
            raise PackageLayoutError(
                f"source changed size during copy ({expected} -> {size} bytes): {source}"
            )
        return self._register(path, role, size, sha256, media_type, derived_from)

    def add_evidence(self, path: str, data: bytes) -> None:
        """Write an unlisted evidence file below ``evidence/``."""
        if not path.startswith(EVIDENCE_PREFIX):
            raise PackageLayoutError(f"evidence must live under {EVIDENCE_PREFIX}: {path!r}")
        self._claim(path, len(data), listed=False).write_bytes(data)

    def write_manifest(
        self,
        *,
        revision_id: str,
        revision_number: int,
        previous_revision_id: str | None,
        ticket: TicketRef,
        captured_at: datetime,
        record_class: str,
        policy_id: str,
        source_digest: str,
        coverage: Coverage,
        producer: Producer,
    ) -> bytes:
        """Write ``manifest.json`` for the files added so far and return its bytes.

        The bytes are parsed back with the strict parser first, so an invalid manifest is
        never written.
        """
        manifest = Manifest(
            format=PACKAGE_FORMAT,
            revision_id=revision_id,
            revision_number=revision_number,
            previous_revision_id=previous_revision_id,
            ticket=ticket,
            captured_at=captured_at,
            record_class=record_class,
            policy_id=policy_id,
            source_digest=source_digest,
            coverage=coverage,
            files=self.files(),
            producer=producer,
        )
        data = serialize_manifest(manifest)
        parse_manifest(data)
        with (self._root / MANIFEST_NAME).open("xb") as handle:
            handle.write(data)
        return data

    def _claim(self, path: str, size: int, *, listed: bool) -> Path:
        """Validate ``path``, reserve its size and return its (parent-created) target."""
        validate_package_path(path)
        if path in _RESERVED_NAMES:
            raise PackageLayoutError(f"reserved package name: {path!r}")
        if listed and not is_manifest_listed(path):
            raise PackageLayoutError(f"path is not a listable package file: {path!r}")
        if path in self._paths:
            raise PackageLayoutError(f"duplicate package path: {path!r}")
        if self._max_bytes is not None and self._total + size > self._max_bytes:
            raise PackageTooLargeError(
                f"package would exceed {self._max_bytes} bytes (adding {size} to {self._total})"
            )
        target = self._root / path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_dir():
                raise PackageLayoutError(f"package path collides with existing entry: {path!r}")
        except (FileExistsError, NotADirectoryError) as exc:
            raise PackageLayoutError(f"package path conflicts with a file: {path!r}") from exc
        self._paths.add(path)
        self._total += size
        return target

    def _register(
        self,
        path: str,
        role: FileRole,
        size: int,
        sha256: str,
        media_type: str,
        derived_from: Sequence[str],
    ) -> ManifestFile:
        entry = ManifestFile(
            path=path,
            role=role,
            size=size,
            sha256=sha256,
            media_type=media_type,
            derived_from=tuple(derived_from),
        )
        self._files[path] = entry
        return entry


def _copy_and_hash(source: Path, target: Path) -> tuple[int, str]:
    """Copy ``source`` into the new file ``target``; return size and digest of what was written."""
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as reader, target.open("xb") as writer:
        while chunk := reader.read(_COPY_CHUNK_BYTES):
            writer.write(chunk)
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


@dataclass(frozen=True, slots=True)
class FileProblem:
    """One discrepancy between a package directory and its manifest."""

    path: str
    kind: Literal["missing", "size", "sha256", "unlisted"]


def _scan_directory(directory: Path) -> tuple[set[str], set[str]]:
    """Return ``(regular files, other entries)`` as relative POSIX paths without following links.

    "Other" is every entry that is neither a regular file nor a directory: symlinks, FIFOs,
    sockets and devices.
    """
    regular: set[str] = set()
    other: set[str] = set()
    for current, dirnames, filenames in os.walk(directory, followlinks=False):
        base = Path(current)
        for name in (*dirnames, *filenames):
            entry = base / name
            relative = entry.relative_to(directory).as_posix()
            try:
                mode = entry.lstat().st_mode
            except OSError:
                continue
            if stat.S_ISREG(mode):
                regular.add(relative)
            elif not stat.S_ISDIR(mode):
                other.add(relative)
    return regular, other


def _check_listed_file(directory: Path, file: ManifestFile) -> FileProblem | None:
    """Compare one regular listed file with its manifest entry."""
    try:
        size, sha256 = hash_file(directory / file.path)
    except OSError:
        return FileProblem(file.path, "missing")
    if size != file.size:
        return FileProblem(file.path, "size")
    if sha256 != file.sha256:
        return FileProblem(file.path, "sha256")
    return None


def verify_package_files(directory: Path, manifest: Manifest) -> list[FileProblem]:
    """Compare a package directory with its manifest.

    Symlinks are never followed. Every entry that is neither a regular file nor a directory
    (symlink, FIFO, socket, device) is reported as an ``unlisted`` problem, and a listed path
    that is such an entry or only reachable through a symlink also counts as ``missing``.
    """
    regular, other = _scan_directory(directory)
    listed = {file.path: file for file in manifest.files}
    problems = [FileProblem(path, "unlisted") for path in other]
    problems.extend(
        FileProblem(path, "unlisted")
        for path in regular
        if path not in listed and is_manifest_listed(path)
    )
    for path, file in listed.items():
        if path not in regular:
            problems.append(FileProblem(path, "missing"))
        elif (problem := _check_listed_file(directory, file)) is not None:
            problems.append(problem)
    return sorted(problems, key=lambda problem: (problem.path, problem.kind))
