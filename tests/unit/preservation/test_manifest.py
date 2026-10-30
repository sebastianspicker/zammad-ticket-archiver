"""Pin the manifest wire format, its strict parser and the revision key layout."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from chronikwerk.preservation.manifest import (
    Coverage,
    CoverageCount,
    FileRole,
    Manifest,
    ManifestError,
    ManifestFile,
    OriginalEmailCoverage,
    Producer,
    TicketRef,
    is_manifest_listed,
    parse_manifest,
    serialize_manifest,
    validate_package_path,
    validate_role_path,
)
from chronikwerk.preservation.revision import (
    PackageLayoutError,
    new_revision_id,
    revision_directory_name,
    revision_key_prefix,
)

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_POLICY_ID = "sha256:" + "1" * 64
_SOURCE_DIGEST = "sha256:" + "2" * 64


def _manifest(**overrides: Any) -> Manifest:
    """Build a manifest with representative values, overriding selected fields."""
    values: dict[str, Any] = {
        "format": "chronikwerk-package/1",
        "revision_id": "0192f3a4-7c1e-7b52-9d0e-3f6a1c2b4d5e",
        "revision_number": 2,
        "previous_revision_id": None,
        "ticket": TicketRef(id=42, number="31042"),
        "captured_at": datetime(2026, 10, 14, 10, 2, 3, tzinfo=UTC),
        "record_class": "it-authorization",
        "policy_id": _POLICY_ID,
        "source_digest": _SOURCE_DIGEST,
        "coverage": Coverage(
            articles=CoverageCount(7, 7),
            attachments=CoverageCount(3, 3),
            original_emails=OriginalEmailCoverage(5, 4, (101,)),
            complete=False,
        ),
        "files": (
            ManifestFile(
                "source/ticket.json", FileRole.SOURCE_TICKET, 10, _HASH_B, "application/json"
            ),
            ManifestFile(
                "reading-copy.pdf",
                FileRole.READING_COPY_ARCHIVAL,
                5,
                _HASH_A,
                "application/pdf",
                ("source/ticket.json",),
            ),
        ),
        "producer": Producer("chronikwerk", "1.2.3"),
    }
    values.update(overrides)
    return Manifest(**values)


def _document(**changes: Any) -> dict[str, Any]:
    """Return the serialized sample manifest as a mutable dictionary."""
    document = json.loads(serialize_manifest(_manifest()))
    document.update(changes)
    return document


def _canonical(document: dict[str, Any]) -> bytes:
    """Encode a manifest dictionary the way the serializer formats it."""
    return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _parse(document: dict[str, Any]) -> Manifest:
    """Parse a manifest dictionary through its canonically formatted JSON bytes."""
    return parse_manifest(_canonical(document))


def test_serialize_manifest_golden_output() -> None:
    expected = """{
  "format": "chronikwerk-package/1",
  "revision_id": "0192f3a4-7c1e-7b52-9d0e-3f6a1c2b4d5e",
  "revision_number": 2,
  "previous_revision_id": null,
  "ticket": {
    "id": 42,
    "number": "31042"
  },
  "captured_at": "2026-10-14T10:02:03Z",
  "record_class": "it-authorization",
  "policy_id": "@P@",
  "source_digest": "@S@",
  "coverage": {
    "articles": {
      "total": 7,
      "included": 7
    },
    "attachments": {
      "total": 3,
      "included": 3
    },
    "original_emails": {
      "expected": 5,
      "included": 4,
      "unavailable": [
        101
      ]
    },
    "complete": false
  },
  "files": [
    {
      "path": "reading-copy.pdf",
      "role": "reading_copy_archival",
      "size": 5,
      "sha256": "@A@",
      "media_type": "application/pdf",
      "derived_from": [
        "source/ticket.json"
      ]
    },
    {
      "path": "source/ticket.json",
      "role": "source_ticket",
      "size": 10,
      "sha256": "@B@",
      "media_type": "application/json",
      "derived_from": []
    }
  ],
  "producer": {
    "name": "chronikwerk",
    "version": "1.2.3"
  }
}
"""
    expected = expected.replace("@A@", _HASH_A).replace("@B@", _HASH_B)
    expected = expected.replace("@P@", _POLICY_ID).replace("@S@", _SOURCE_DIGEST)
    assert serialize_manifest(_manifest()).decode("utf-8") == expected


def test_serialize_manifest_key_order_is_contractual() -> None:
    document = json.loads(serialize_manifest(_manifest()))
    assert list(document) == [
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
    ]
    assert list(document["files"][0]) == [
        "path",
        "role",
        "size",
        "sha256",
        "media_type",
        "derived_from",
    ]
    assert list(document["coverage"]["original_emails"]) == [
        "expected",
        "included",
        "unavailable",
    ]


def test_serialize_converts_aware_datetime_to_utc_and_keeps_unicode() -> None:
    plus_two = timezone(timedelta(hours=2))
    manifest = _manifest(
        captured_at=datetime(2026, 10, 14, 12, 2, 3, tzinfo=plus_two),
        producer=Producer("chronikwerk", "ü"),
    )
    data = serialize_manifest(manifest)
    assert b'"captured_at": "2026-10-14T10:02:03Z"' in data
    assert "ü".encode() in data


def test_serialize_rejects_naive_datetime() -> None:
    with pytest.raises(ManifestError, match="timezone"):
        serialize_manifest(_manifest(captured_at=datetime(2026, 10, 14, 10, 2, 3)))


def test_parse_round_trip() -> None:
    manifest = _manifest(previous_revision_id="0192a7c0-0000-7000-8000-000000000000")
    parsed = parse_manifest(serialize_manifest(manifest))
    assert parsed.revision_number == 2
    assert parsed.previous_revision_id == "0192a7c0-0000-7000-8000-000000000000"
    assert parsed.captured_at == manifest.captured_at
    assert [file.path for file in parsed.files] == ["reading-copy.pdf", "source/ticket.json"]
    assert parsed.coverage == manifest.coverage
    assert serialize_manifest(parsed) == serialize_manifest(manifest)


@pytest.mark.parametrize("bad_hash", ["A" * 64, "a" * 63, "g" * 64, ""])
def test_parse_rejects_bad_sha256(bad_hash: str) -> None:
    document = _document()
    document["files"][0]["sha256"] = bad_hash
    with pytest.raises(ManifestError, match="sha256"):
        _parse(document)


@pytest.mark.parametrize(
    "bad_path",
    ["../x", "a/../x", "/abs", "a//b", "a\\b", "./x", "a/", "", "manifest.json", "evidence/x.der"],
)
def test_parse_rejects_bad_paths(bad_path: str) -> None:
    document = _document()
    document["files"][0]["path"] = bad_path
    with pytest.raises(ManifestError):
        _parse(document)


def test_parse_rejects_unknown_role() -> None:
    document = _document()
    document["files"][0]["role"] = "mystery"
    with pytest.raises(ManifestError, match="role"):
        _parse(document)


def test_parse_rejects_duplicate_paths() -> None:
    document = _document()
    document["files"][1] = dict(document["files"][0])
    with pytest.raises(ManifestError, match="duplicate"):
        _parse(document)


def test_parse_rejects_derived_from_unlisted_file() -> None:
    document = _document()
    document["files"][0]["derived_from"] = ["missing.json"]
    with pytest.raises(ManifestError, match="derived"):
        _parse(document)


def test_parse_rejects_wrong_format_negative_size_and_bad_timestamps() -> None:
    with pytest.raises(ManifestError, match="format"):
        _parse(_document(format="chronikwerk-package/2"))
    negative = _document()
    negative["files"][0]["size"] = -1
    with pytest.raises(ManifestError, match="size"):
        _parse(negative)
    for text in ("2026-10-14T10:02:03", "2026-10-14T10:02:03+02:00", "2026-13-14T10:02:03Z"):
        with pytest.raises(ManifestError, match="captured_at"):
            _parse(_document(captured_at=text))


def test_parse_rejects_non_json_and_unexpected_keys() -> None:
    with pytest.raises(ManifestError):
        parse_manifest(b"\xff")
    with pytest.raises(ManifestError):
        parse_manifest(b"not json")
    with pytest.raises(ManifestError, match="keys"):
        _parse({**_document(), "extra": 1})


def test_parse_rejects_duplicate_json_keys() -> None:
    data = _canonical(_document()).replace(
        b'  "revision_number": 2,\n', b'  "revision_number": 2,\n  "revision_number": 3,\n'
    )
    with pytest.raises(ManifestError, match="duplicate JSON key"):
        parse_manifest(data)


def test_parse_rejects_deep_nesting_and_lone_surrogates_as_manifest_errors() -> None:
    with pytest.raises(ManifestError):
        parse_manifest(b"[" * 200_000 + b"]" * 200_000)
    data = _canonical(_document()).replace(b'"1.2.3"', b'"\\ud800"')
    with pytest.raises(ManifestError):
        parse_manifest(data)


def test_serialize_rejects_unencodable_text() -> None:
    with pytest.raises(ManifestError, match="UTF-8"):
        serialize_manifest(_manifest(producer=Producer("chronikwerk", "\ud800")))


@pytest.mark.parametrize(
    "encode",
    [
        lambda document: json.dumps(document).encode("utf-8"),
        lambda document: json.dumps(document, indent=2).encode("utf-8") + b"\n",
        lambda document: json.dumps(document, indent=4, ensure_ascii=False).encode() + b"\n",
        lambda document: json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8"),
        lambda document: _canonical(dict(reversed(list(document.items())))),
        lambda document: _canonical({**document, "files": list(reversed(document["files"]))}),
    ],
    ids=["compact", "ascii-escaped", "indent-4", "no-newline", "key-order", "unsorted-files"],
)
def test_parse_requires_canonical_bytes(encode: Any) -> None:
    document = json.loads(serialize_manifest(_manifest(producer=Producer("chronikwerk", "ü"))))
    with pytest.raises(ManifestError, match="canonical"):
        parse_manifest(encode(document))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revision_id", "has space"),
        ("revision_id", "a" * 65),
        ("revision_id", "../x"),
        ("previous_revision_id", "a/b"),
        ("record_class", "Upper"),
        ("record_class", ""),
        ("policy_id", "sha256:p"),
        ("policy_id", "sha512:" + "1" * 64),
        ("source_digest", "sha256:" + "A" * 64),
        ("source_digest", "1" * 64),
    ],
)
def test_parse_rejects_malformed_identifiers(field: str, value: str) -> None:
    with pytest.raises(ManifestError, match=field):
        _parse(_document(**{field: value}))


@pytest.mark.parametrize(
    "coverage",
    [
        Coverage(CoverageCount(7, 6), CoverageCount(3, 3), OriginalEmailCoverage(5, 5), True),
        Coverage(CoverageCount(7, 7), CoverageCount(3, 2), OriginalEmailCoverage(5, 5), True),
        Coverage(CoverageCount(7, 7), CoverageCount(3, 3), OriginalEmailCoverage(5, 4), True),
        Coverage(CoverageCount(7, 7), CoverageCount(3, 3), OriginalEmailCoverage(5, 5, (9,)), True),
    ],
)
def test_parse_rejects_complete_coverage_with_gaps(coverage: Coverage) -> None:
    with pytest.raises(ManifestError, match="complete"):
        parse_manifest(serialize_manifest(_manifest(coverage=coverage)))


def test_parse_accepts_complete_coverage_with_unavailable_emails() -> None:
    coverage = Coverage(
        CoverageCount(7, 7), CoverageCount(3, 3), OriginalEmailCoverage(5, 4, (101,)), True
    )
    parsed = parse_manifest(serialize_manifest(_manifest(coverage=coverage)))
    assert parsed.coverage.complete


def test_parse_rejects_file_derived_from_itself() -> None:
    document = _document()
    document["files"][0]["derived_from"] = ["reading-copy.pdf"]
    with pytest.raises(ManifestError, match="itself"):
        _parse(document)


@pytest.mark.parametrize(
    ("role", "path"),
    [
        ("reading_copy_archival", "reading-copy-ua.pdf"),
        ("reading_copy_accessible", "reading-copy.pdf"),
        ("source_ticket", "source/articles.json"),
        ("source_articles", "source/ticket.json"),
        ("original_email", "source/articles/101.txt"),
        ("original_email", "attachments/101.eml"),
        ("original_email", "source/articles/.eml"),
        ("attachment", "source/x.bin"),
        ("capture_description", "events/capture.json"),
        ("mail_evidence", "attachments/01-dsn"),
    ],
)
def test_parse_rejects_role_path_mismatch(role: str, path: str) -> None:
    document = _document()
    document["files"] = [
        {
            "path": path,
            "role": role,
            "size": 1,
            "sha256": _HASH_A,
            "media_type": "x/y",
            "derived_from": [],
        }
    ]
    with pytest.raises(ManifestError, match=role):
        _parse(document)


@pytest.mark.parametrize(
    ("role", "path"),
    [
        (FileRole.READING_COPY_ARCHIVAL, "reading-copy.pdf"),
        (FileRole.READING_COPY_ACCESSIBLE, "reading-copy-ua.pdf"),
        (FileRole.SOURCE_TICKET, "source/ticket.json"),
        (FileRole.SOURCE_ARTICLES, "source/articles.json"),
        (FileRole.ORIGINAL_EMAIL, "source/articles/101.eml"),
        (FileRole.ATTACHMENT, "attachments/101/501/file.pdf"),
        (FileRole.CAPTURE_DESCRIPTION, "capture.json"),
        (FileRole.MAIL_EVIDENCE, "events/01-dsn.eml"),
    ],
)
def test_validate_role_path_accepts_the_layout(role: FileRole, path: str) -> None:
    assert validate_role_path(path, role) == path


def test_validate_package_path_and_listing_rules() -> None:
    assert validate_package_path("attachments/1/2/file.txt") == "attachments/1/2/file.txt"
    with pytest.raises(PackageLayoutError):
        validate_package_path("a/\x00b")
    assert is_manifest_listed("capture.json")
    for unlisted in ("manifest.json", "manifest.json.p7s", "manifest.json.p7s.tst", "evidence/a"):
        assert not is_manifest_listed(unlisted)


def test_revision_ids_are_uuid7_and_directory_names_are_padded() -> None:
    revision_id = new_revision_id()
    assert revision_id[14] == "7"
    assert revision_id != new_revision_id()
    assert revision_directory_name(2, "abc") == "r0002_abc"
    for number in (0, 10000, -1):
        with pytest.raises(PackageLayoutError):
            revision_directory_name(number, "abc")


def test_revision_key_prefix_layout() -> None:
    prefix = revision_key_prefix(
        record_class="it-authorization",
        archive_user="alice",
        archive_path=["2026", "IT"],
        ticket_number="31042",
        revision_number=2,
        revision_id="rid",
    )
    assert prefix == "it-authorization/alice/2026/IT/Ticket-31042/r0002_rid/"
    dotted = revision_key_prefix(
        record_class="it-authorization",
        archive_user="a.b_c-1",
        archive_path=["x.y"],
        ticket_number="31042",
        revision_number=2,
        revision_id="rid",
    )
    assert dotted == "it-authorization/a.b_c-1/x.y/Ticket-31042/r0002_rid/"


@pytest.mark.parametrize(
    "overrides",
    [
        {"record_class": "Upper"},
        {"record_class": ""},
        {"record_class": "a" * 65},
        {"archive_user": ""},
        {"archive_user": ".."},
        {"archive_user": "a/b"},
        {"archive_user": "a\\b"},
        {"archive_user": "a\x00b"},
        {"archive_user": "x" * 129},
        {"archive_user": "a b"},
        {"archive_user": "ä"},
        {"archive_user": "a:b"},
        {"archive_path": ["ok", ".."]},
        {"archive_path": ["ok", "a*b"]},
        {"archive_path": ["y" * 129]},
        {"archive_path": ["ok", "."]},
        {"archive_path": ["ok", ""]},
        {"ticket_number": "12/3"},
        {"ticket_number": "12 3"},
        {"ticket_number": ""},
        {"revision_number": 0},
    ],
)
def test_revision_key_prefix_rejects_unsafe_segments(overrides: dict[str, Any]) -> None:
    arguments: dict[str, Any] = {
        "record_class": "pilot-test",
        "archive_user": "alice",
        "archive_path": ["2026"],
        "ticket_number": "31042",
        "revision_number": 1,
        "revision_id": "rid",
    }
    arguments.update(overrides)
    with pytest.raises(PackageLayoutError):
        revision_key_prefix(**arguments)
