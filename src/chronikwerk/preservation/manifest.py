"""Manifest format of a preservation package: model, serialization and strict parsing."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from chronikwerk.failures import PermanentError
from chronikwerk.preservation.revision import (
    RECORD_CLASS_PATTERN,
    REVISION_ID_PATTERN,
    PackageLayoutError,
)

__all__ = [
    "EVIDENCE_PREFIX",
    "MANIFEST_NAME",
    "PACKAGE_FORMAT",
    "SEAL_NAME",
    "TOKEN_NAME",
    "Coverage",
    "CoverageCount",
    "FileRole",
    "Manifest",
    "ManifestError",
    "ManifestFile",
    "OriginalEmailCoverage",
    "Producer",
    "TicketRef",
    "hash_file",
    "is_manifest_listed",
    "parse_manifest",
    "serialize_manifest",
    "validate_package_path",
    "validate_role_path",
]

PACKAGE_FORMAT = "chronikwerk-package/1"
MANIFEST_NAME = "manifest.json"
SEAL_NAME = "manifest.json.p7s"
TOKEN_NAME = "manifest.json.p7s.tst"
EVIDENCE_PREFIX = "evidence/"
_RESERVED_NAMES = frozenset({MANIFEST_NAME, SEAL_NAME, TOKEN_NAME})
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PREFIXED_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_CAPTURED_AT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_CAPTURED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_HASH_CHUNK_BYTES = 1024 * 1024


class ManifestError(PermanentError):
    """A manifest is malformed or violates the package format."""


class FileRole(StrEnum):
    """Role of one file inside a package."""

    READING_COPY_ARCHIVAL = "reading_copy_archival"
    READING_COPY_ACCESSIBLE = "reading_copy_accessible"
    SOURCE_TICKET = "source_ticket"
    SOURCE_ARTICLES = "source_articles"
    ORIGINAL_EMAIL = "original_email"
    ATTACHMENT = "attachment"
    CAPTURE_DESCRIPTION = "capture_description"
    MAIL_EVIDENCE = "mail_evidence"


@dataclass(frozen=True, slots=True)
class ManifestFile:
    """One listed package file with its size and digest."""

    path: str
    role: FileRole
    size: int
    sha256: str
    media_type: str
    derived_from: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CoverageCount:
    """Total and included item counts for one kind of source item."""

    total: int
    included: int


@dataclass(frozen=True, slots=True)
class OriginalEmailCoverage:
    """Original-email coverage including the articles whose EML was unavailable."""

    expected: int
    included: int
    unavailable: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class Coverage:
    """What the capture contains compared with what the source holds."""

    articles: CoverageCount
    attachments: CoverageCount
    original_emails: OriginalEmailCoverage
    complete: bool


@dataclass(frozen=True, slots=True)
class TicketRef:
    """Identity of the captured ticket."""

    id: int
    number: str


@dataclass(frozen=True, slots=True)
class Producer:
    """Software that produced the package."""

    name: str
    version: str


@dataclass(frozen=True, slots=True)
class Manifest:
    """Parsed package manifest."""

    format: str
    revision_id: str
    revision_number: int
    previous_revision_id: str | None
    ticket: TicketRef
    captured_at: datetime
    record_class: str
    policy_id: str
    source_digest: str
    coverage: Coverage
    files: tuple[ManifestFile, ...]
    producer: Producer


def validate_package_path(path: str) -> str:
    """Return ``path`` if it is a relative POSIX package path, else raise."""
    if not path or path.startswith("/") or "\\" in path or "\x00" in path:
        raise PackageLayoutError(f"invalid package path: {path!r}")
    if any(segment in {"", ".", ".."} for segment in path.split("/")):
        raise PackageLayoutError(f"invalid package path: {path!r}")
    return path


# Each role either names one fixed path or a ``(prefix, suffix)`` pattern below a directory.
_ROLE_PATHS: dict[FileRole, str] = {
    FileRole.READING_COPY_ARCHIVAL: "reading-copy.pdf",
    FileRole.READING_COPY_ACCESSIBLE: "reading-copy-ua.pdf",
    FileRole.SOURCE_TICKET: "source/ticket.json",
    FileRole.SOURCE_ARTICLES: "source/articles.json",
    FileRole.CAPTURE_DESCRIPTION: "capture.json",
}
_ROLE_PATH_PATTERNS: dict[FileRole, tuple[str, str]] = {
    FileRole.ORIGINAL_EMAIL: ("source/articles/", ".eml"),
    FileRole.ATTACHMENT: ("attachments/", ""),
    FileRole.MAIL_EVIDENCE: ("events/", ""),
}


def validate_role_path(path: str, role: FileRole) -> str:
    """Return ``path`` if it is where files of ``role`` live in a package, else raise."""
    if role in _ROLE_PATHS:
        if path != _ROLE_PATHS[role]:
            raise PackageLayoutError(f"{role} must be stored as {_ROLE_PATHS[role]!r}: {path!r}")
        return path
    prefix, suffix = _ROLE_PATH_PATTERNS[role]
    if not path.startswith(prefix) or not path.endswith(suffix):
        raise PackageLayoutError(f"{role} must be stored as {prefix}…{suffix}: {path!r}")
    if len(path) <= len(prefix) + len(suffix):
        raise PackageLayoutError(f"{role} path has no name: {path!r}")
    return path


def is_manifest_listed(path: str) -> bool:
    """Return whether ``path`` belongs in the manifest file list."""
    return path not in _RESERVED_NAMES and not path.startswith(EVIDENCE_PREFIX)


def hash_file(path: Path) -> tuple[int, str]:
    """Return ``(size, sha256 hex)`` of a file, read in 1 MiB chunks."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _format_captured_at(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ManifestError("captured_at must be timezone-aware")
    return value.astimezone(UTC).strftime(_CAPTURED_AT_FORMAT)


def _file_to_dict(file: ManifestFile) -> dict[str, Any]:
    return {
        "path": file.path,
        "role": str(file.role),
        "size": file.size,
        "sha256": file.sha256,
        "media_type": file.media_type,
        "derived_from": list(file.derived_from),
    }


def _coverage_to_dict(coverage: Coverage) -> dict[str, Any]:
    emails = coverage.original_emails
    return {
        "articles": {
            "total": coverage.articles.total,
            "included": coverage.articles.included,
        },
        "attachments": {
            "total": coverage.attachments.total,
            "included": coverage.attachments.included,
        },
        "original_emails": {
            "expected": emails.expected,
            "included": emails.included,
            "unavailable": list(emails.unavailable),
        },
        "complete": coverage.complete,
    }


def serialize_manifest(manifest: Manifest) -> bytes:
    """Serialize ``manifest`` in the contractual key order as UTF-8 bytes."""
    document = {
        "format": manifest.format,
        "revision_id": manifest.revision_id,
        "revision_number": manifest.revision_number,
        "previous_revision_id": manifest.previous_revision_id,
        "ticket": {"id": manifest.ticket.id, "number": manifest.ticket.number},
        "captured_at": _format_captured_at(manifest.captured_at),
        "record_class": manifest.record_class,
        "policy_id": manifest.policy_id,
        "source_digest": manifest.source_digest,
        "coverage": _coverage_to_dict(manifest.coverage),
        "files": [_file_to_dict(file) for file in sorted(manifest.files, key=lambda f: f.path)],
        "producer": {"name": manifest.producer.name, "version": manifest.producer.version},
    }
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ManifestError(f"manifest text cannot be encoded as UTF-8: {exc}") from exc


def _require_mapping(value: object, label: str, keys: tuple[str, ...]) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must be an object")
    if set(value) != set(keys):
        raise ManifestError(f"{label} must have exactly the keys: {', '.join(keys)}")
    return value


def _require_int(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ManifestError(f"{label} must be an integer >= {minimum}")
    return value


def _require_str(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"{label} must be a string")
    return value


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ManifestError(f"{label} must be a boolean")
    return value


def _require_list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManifestError(f"{label} must be an array")
    return value


def _require_pattern(value: object, label: str, pattern: re.Pattern[str]) -> str:
    text = _require_str(value, label)
    if not pattern.fullmatch(text):
        raise ManifestError(f"{label} must match {pattern.pattern}")
    return text


def _parse_captured_at(value: object) -> datetime:
    text = _require_str(value, "captured_at")
    if not _CAPTURED_AT_PATTERN.fullmatch(text):
        raise ManifestError("captured_at must look like YYYY-MM-DDTHH:MM:SSZ")
    try:
        return datetime.strptime(text, _CAPTURED_AT_FORMAT).replace(tzinfo=UTC)
    except ValueError as exc:
        raise ManifestError("captured_at is not a valid timestamp") from exc


def _parse_count(value: object, label: str) -> CoverageCount:
    data = _require_mapping(value, label, ("total", "included"))
    return CoverageCount(
        total=_require_int(data["total"], f"{label}.total"),
        included=_require_int(data["included"], f"{label}.included"),
    )


def _parse_coverage(value: object) -> Coverage:
    data = _require_mapping(
        value, "coverage", ("articles", "attachments", "original_emails", "complete")
    )
    emails = _require_mapping(
        data["original_emails"],
        "coverage.original_emails",
        ("expected", "included", "unavailable"),
    )
    unavailable = _require_list(emails["unavailable"], "coverage.original_emails.unavailable")
    coverage = Coverage(
        articles=_parse_count(data["articles"], "coverage.articles"),
        attachments=_parse_count(data["attachments"], "coverage.attachments"),
        original_emails=OriginalEmailCoverage(
            expected=_require_int(emails["expected"], "coverage.original_emails.expected"),
            included=_require_int(emails["included"], "coverage.original_emails.included"),
            unavailable=tuple(
                _require_int(item, "coverage.original_emails.unavailable[]", minimum=1)
                for item in unavailable
            ),
        ),
        complete=_require_bool(data["complete"], "coverage.complete"),
    )
    if coverage.complete and not _is_fully_covered(coverage):
        raise ManifestError("coverage.complete is true but the counts show missing items")
    return coverage


def _is_fully_covered(coverage: Coverage) -> bool:
    """Return whether every article, attachment and expected email is accounted for."""
    emails = coverage.original_emails
    return (
        coverage.articles.included == coverage.articles.total
        and coverage.attachments.included == coverage.attachments.total
        and emails.included + len(emails.unavailable) == emails.expected
    )


def _parse_role(value: object) -> FileRole:
    try:
        return FileRole(_require_str(value, "files[].role"))
    except ValueError as exc:
        raise ManifestError(f"unknown file role: {value!r}") from exc


def _parse_listed_path(value: object) -> str:
    path = _require_str(value, "files[].path")
    try:
        validate_package_path(path)
    except PackageLayoutError as exc:
        raise ManifestError(str(exc)) from exc
    if not is_manifest_listed(path):
        raise ManifestError(f"path must not be listed in the manifest: {path!r}")
    return path


def _parse_file(value: object) -> ManifestFile:
    data = _require_mapping(
        value, "files[]", ("path", "role", "size", "sha256", "media_type", "derived_from")
    )
    sha256 = _require_str(data["sha256"], "files[].sha256")
    if not _SHA256_PATTERN.fullmatch(sha256):
        raise ManifestError("files[].sha256 must be 64 lowercase hex characters")
    derived_from = _require_list(data["derived_from"], "files[].derived_from")
    path = _parse_listed_path(data["path"])
    role = _parse_role(data["role"])
    try:
        validate_role_path(path, role)
    except PackageLayoutError as exc:
        raise ManifestError(str(exc)) from exc
    return ManifestFile(
        path=path,
        role=role,
        size=_require_int(data["size"], "files[].size"),
        sha256=sha256,
        media_type=_require_str(data["media_type"], "files[].media_type"),
        derived_from=tuple(_require_str(item, "files[].derived_from[]") for item in derived_from),
    )


def _parse_files(value: object) -> tuple[ManifestFile, ...]:
    files = tuple(_parse_file(item) for item in _require_list(value, "files"))
    paths = [file.path for file in files]
    if len(set(paths)) != len(paths):
        raise ManifestError("duplicate file paths in manifest")
    known = set(paths)
    for file in files:
        for source in file.derived_from:
            if source == file.path:
                raise ManifestError(f"{file.path} must not be derived from itself")
            if source not in known:
                raise ManifestError(f"{file.path} is derived from unlisted file {source!r}")
    return files


def _parse_previous(value: object) -> str | None:
    if value is None:
        return None
    return _require_pattern(value, "previous_revision_id", REVISION_ID_PATTERN)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ManifestError(f"duplicate JSON key in manifest: {key!r}")
        document[key] = value
    return document


def _load_json(data: bytes) -> object:
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ManifestError(f"manifest is not valid UTF-8 JSON: {type(exc).__name__}") from exc


def parse_manifest(data: bytes) -> Manifest:
    """Parse and strictly validate manifest bytes, raising ``ManifestError`` on any defect.

    Only the canonical serialization is accepted: the bytes must equal what
    ``serialize_manifest`` produces for the parsed manifest.
    """
    manifest = _parse_document(_load_json(data))
    if serialize_manifest(manifest) != data:
        raise ManifestError("manifest is not in canonical form")
    return manifest


def _parse_document(document: object) -> Manifest:
    root = _require_mapping(
        document,
        "manifest",
        (
            "format",
            "revision_id",
            "revision_number",
            "previous_revision_id",
            "ticket",
            "captured_at",
            "record_class",
            "policy_id",
            "source_digest",
            "coverage",
            "files",
            "producer",
        ),
    )
    if root["format"] != PACKAGE_FORMAT:
        raise ManifestError(f"unsupported package format: {root['format']!r}")
    ticket = _require_mapping(root["ticket"], "ticket", ("id", "number"))
    producer = _require_mapping(root["producer"], "producer", ("name", "version"))
    return Manifest(
        format=PACKAGE_FORMAT,
        revision_id=_require_pattern(root["revision_id"], "revision_id", REVISION_ID_PATTERN),
        revision_number=_require_int(root["revision_number"], "revision_number", minimum=1),
        previous_revision_id=_parse_previous(root["previous_revision_id"]),
        ticket=TicketRef(
            id=_require_int(ticket["id"], "ticket.id"),
            number=_require_str(ticket["number"], "ticket.number"),
        ),
        captured_at=_parse_captured_at(root["captured_at"]),
        record_class=_require_pattern(root["record_class"], "record_class", RECORD_CLASS_PATTERN),
        policy_id=_require_pattern(root["policy_id"], "policy_id", _PREFIXED_SHA256_PATTERN),
        source_digest=_require_pattern(
            root["source_digest"], "source_digest", _PREFIXED_SHA256_PATTERN
        ),
        coverage=_parse_coverage(root["coverage"]),
        files=_parse_files(root["files"]),
        producer=Producer(
            name=_require_str(producer["name"], "producer.name"),
            version=_require_str(producer["version"], "producer.version"),
        ),
    )
