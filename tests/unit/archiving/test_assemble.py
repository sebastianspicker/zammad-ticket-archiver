"""Pin package assembly: reading copies, the capture description and the written manifest."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from chronikwerk.archiving.assemble import assemble_package, capture_description_bytes
from chronikwerk.preservation.manifest import MANIFEST_NAME, FileRole
from chronikwerk.preservation.package import verify_package_files
from chronikwerk.preservation.revision import new_revision_id
from tests.support.commit_harness import (
    PRODUCER,
    RULE,
    assemble,
    capture,
    reading_copies,
)


def test_assembly_lists_reading_copies_and_capture_description(tmp_path: Path) -> None:
    captured = capture(tmp_path)
    assembled = assemble(captured, revision_id=new_revision_id(), request_id="req-1")
    files = {file.path: file for file in assembled.manifest.files}
    archival = files["reading-copy.pdf"]
    accessible = files["reading-copy-ua.pdf"]
    assert archival.role is FileRole.READING_COPY_ARCHIVAL
    assert accessible.role is FileRole.READING_COPY_ACCESSIBLE
    for copy in (archival, accessible):
        assert copy.media_type == "application/pdf"
        assert copy.derived_from == ("source/ticket.json", "source/articles.json")
    description = files["capture.json"]
    assert (description.role, description.media_type) == (
        FileRole.CAPTURE_DESCRIPTION,
        "application/json",
    )
    assert (assembled.build_dir / MANIFEST_NAME).read_bytes() == assembled.manifest_bytes
    assert verify_package_files(assembled.build_dir, assembled.manifest) == []


def test_manifest_carries_capture_facts(tmp_path: Path) -> None:
    captured = capture(tmp_path)
    revision_id = new_revision_id()
    manifest = assemble(captured, revision_id=revision_id, request_id="req-1").manifest
    assert manifest.revision_id == revision_id
    assert (manifest.ticket.id, manifest.ticket.number) == (42, "31042")
    assert manifest.record_class == captured.record_class
    assert manifest.source_digest == captured.source_digest
    assert manifest.coverage == captured.coverage
    assert manifest.producer == PRODUCER


def test_capture_description_records_capture_and_caller_facts(tmp_path: Path) -> None:
    captured = capture(tmp_path)
    assembled = assemble(captured, revision_id="rev-1", request_id="req-1")
    data = (assembled.build_dir / "capture.json").read_bytes()
    document = json.loads(data)
    assert data == capture_description_bytes(document)
    assert data.endswith(b"}\n")
    assert document["revision_id"] == "rev-1"
    assert document["producer"] == {"name": "chronikwerk", "version": "0.3.0a1"}
    assert document["request"] == {"id": "req-1", "source": "webhook"}
    assert document["source"]["origin"] == "https://zammad.example.org"
    assert document["record_class"] == "it-authorization"
    assert document["retention"]["rule"] == RULE
    finished = captured.collection_finished
    assert datetime.fromisoformat(document["collection"]["finished"]) == finished
    assert document["collection"]["attempts"] == 1
    assert document["inventories"]["before"] == captured.inventory_before
    assert document["inventories"]["after"] == captured.inventory_after
    assert document["coverage"]["original_emails"] == {
        "expected": 1,
        "included": 1,
        "unavailable": [],
    }
    assert document["mail_auth"]["101"]["dkim_signature_domains"] == ["example.com"]
    assert document["mail_auth"]["101"]["verified"] is False
    assert document["mail_auth"]["101"]["order"] == "as-in-message, topmost first"
    assert document["limits"]["max_attachment_bytes"] == 1024 * 1024


def test_capture_description_serialization_is_deterministic() -> None:
    first = capture_description_bytes({"b": 1, "a": {"z": "ä", "y": [1, 2]}})
    second = capture_description_bytes({"a": {"y": [1, 2], "z": "ä"}, "b": 1})
    assert first == second
    expected = (
        '{\n  "a": {\n    "y": [\n      1,\n      2\n    ],\n    "z": "ä"\n  },\n  "b": 1\n}\n'
    )
    assert first == expected.encode("utf-8")


def test_missing_accessible_copy_is_simply_not_listed(tmp_path: Path) -> None:
    captured = capture(tmp_path)
    assembled = assemble_package(
        captured,
        reading_copies(accessible=False),
        revision_id="rev-1",
        revision_number=1,
        previous_revision_id=None,
        policy_id="sha256:" + "1" * 64,
        captured_at=captured.collection_finished,
        producer=PRODUCER,
        capture_description={},
    )
    paths = {file.path for file in assembled.manifest.files}
    assert "reading-copy.pdf" in paths
    assert "reading-copy-ua.pdf" not in paths


def test_caller_cannot_override_derived_capture_facts(tmp_path: Path) -> None:
    captured = capture(tmp_path)
    with pytest.raises(ValueError, match="coverage"):
        assemble_package(
            captured,
            reading_copies(),
            revision_id="rev-1",
            revision_number=1,
            previous_revision_id=None,
            policy_id="sha256:" + "1" * 64,
            captured_at=captured.collection_finished,
            producer=PRODUCER,
            capture_description={"coverage": {"complete": True}},
        )
    assert not (captured.build_dir / "capture.json").exists()
