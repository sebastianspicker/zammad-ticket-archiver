"""Pin package building, size limits and verification of package files."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chronikwerk.preservation import package as package_module
from chronikwerk.preservation.manifest import (
    Coverage,
    CoverageCount,
    FileRole,
    Manifest,
    ManifestError,
    OriginalEmailCoverage,
    Producer,
    TicketRef,
    parse_manifest,
)
from chronikwerk.preservation.package import (
    FileProblem,
    PackageBuilder,
    PackageTooLargeError,
    sanitize_filename,
    verify_package_files,
)
from chronikwerk.preservation.revision import PackageLayoutError


def _builder(tmp_path: Path, **kwargs: int | None) -> tuple[PackageBuilder, Path]:
    """Create an empty build directory and a builder bound to it."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    return PackageBuilder(build_dir, **kwargs), build_dir


def _write_manifest(builder: PackageBuilder) -> bytes:
    """Write a manifest with fixed sample metadata."""
    return builder.write_manifest(
        revision_id="rid",
        revision_number=1,
        previous_revision_id=None,
        ticket=TicketRef(1, "10001"),
        captured_at=datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC),
        record_class="pilot-test",
        policy_id="sha256:" + "1" * 64,
        source_digest="sha256:" + "2" * 64,
        coverage=Coverage(
            CoverageCount(1, 1), CoverageCount(0, 0), OriginalEmailCoverage(0, 0), True
        ),
        producer=Producer("chronikwerk", "0"),
    )


def _populated(tmp_path: Path) -> tuple[Path, Manifest]:
    """Build a sealed-less package with two listed files and one evidence file."""
    builder, build_dir = _builder(tmp_path)
    builder.add_bytes("capture.json", b"{}", role=FileRole.CAPTURE_DESCRIPTION, media_type="x/y")
    builder.add_bytes(
        "source/ticket.json", b"ticket", role=FileRole.SOURCE_TICKET, media_type="x/y"
    )
    builder.add_evidence("evidence/validation/01-cert.der", b"der")
    manifest = parse_manifest(_write_manifest(builder))
    return build_dir, manifest


def test_sanitize_filename_rules() -> None:
    assert sanitize_filename("Übersicht Äpfel.pdf") == "Ubersicht_Apfel.pdf"
    assert sanitize_filename("a  b///c") == "a_b_c"
    assert sanitize_filename("...hidden") == "_...hidden"
    assert sanitize_filename(".htaccess") == "_.htaccess"
    assert sanitize_filename("．env") == "_.env"
    assert sanitize_filename("???") == "_"
    assert sanitize_filename("") == "file"
    assert sanitize_filename("...") == "file"
    assert sanitize_filename("..") == "file"
    long = sanitize_filename("x" * 300 + ".pdf")
    assert len(long) == 120
    assert long.endswith(".pdf")
    assert len(sanitize_filename("y" * 300)) == 120
    dotted = sanitize_filename("." + "z" * 300)
    assert len(dotted) == 120
    assert dotted.startswith("_.")
    assert sanitize_filename("x" * 119 + "." + "y" * 200) == "x" * 119


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("CON", "_CON"),
        ("nul.txt", "_nul.txt"),
        ("Aux.tar.gz", "_Aux.tar.gz"),
        ("com1", "_com1"),
        ("LPT9.log", "_LPT9.log"),
        ("PRN", "_PRN"),
        ("COM10", "COM10"),
        ("console.txt", "console.txt"),
        ("report.pdf. . ", "report.pdf"),
        ("NUL. ", "_NUL"),
        ("notes...", "notes"),
        (". .", "file"),
        (".hidden. ", "_.hidden"),
    ],
)
def test_sanitize_filename_avoids_windows_reserved_names(name: str, expected: str) -> None:
    assert sanitize_filename(name) == expected


def test_builder_requires_existing_empty_directory(tmp_path: Path) -> None:
    with pytest.raises(PackageLayoutError):
        PackageBuilder(tmp_path / "missing")
    _, build_dir = _builder(tmp_path)
    (build_dir / "leftover").write_bytes(b"x")
    with pytest.raises(PackageLayoutError):
        PackageBuilder(build_dir)


def test_builder_records_files_sorted_and_tracks_total(tmp_path: Path) -> None:
    builder, build_dir = _builder(tmp_path)
    builder.add_bytes("attachments/b.txt", b"bb", role=FileRole.ATTACHMENT, media_type="t/p")
    source = tmp_path / "src.bin"
    source.write_bytes(b"content")
    entry = builder.add_file(
        "attachments/a/c.bin",
        source,
        role=FileRole.ATTACHMENT,
        media_type="application/octet-stream",
        derived_from=("attachments/b.txt",),
    )
    assert entry.sha256 == hashlib.sha256(b"content").hexdigest()
    assert entry.size == 7
    assert source.exists()
    assert [file.path for file in builder.files()] == ["attachments/a/c.bin", "attachments/b.txt"]
    assert builder.total_bytes == 9
    assert (build_dir / "attachments" / "a" / "c.bin").read_bytes() == b"content"


def test_builder_add_file_can_move(tmp_path: Path) -> None:
    builder, build_dir = _builder(tmp_path)
    source = tmp_path / "src.bin"
    source.write_bytes(b"content")
    builder.add_file(
        "attachments/x.bin", source, role=FileRole.ATTACHMENT, media_type="a/b", move=True
    )
    assert not source.exists()
    assert (build_dir / "attachments" / "x.bin").read_bytes() == b"content"


def test_builder_rejects_reserved_duplicate_and_bad_paths(tmp_path: Path) -> None:
    builder, _ = _builder(tmp_path)
    for reserved in ("manifest.json", "manifest.json.p7s", "manifest.json.p7s.tst"):
        with pytest.raises(PackageLayoutError):
            builder.add_bytes(reserved, b"x", role=FileRole.ATTACHMENT, media_type="a/b")
    for bad in ("../x", "/x", "evidence/x", "a//b"):
        with pytest.raises(PackageLayoutError):
            builder.add_bytes(bad, b"x", role=FileRole.ATTACHMENT, media_type="a/b")
    builder.add_bytes("attachments/a", b"x", role=FileRole.ATTACHMENT, media_type="a/b")
    with pytest.raises(PackageLayoutError):
        builder.add_bytes("attachments/a", b"y", role=FileRole.ATTACHMENT, media_type="a/b")
    with pytest.raises(PackageLayoutError):
        builder.add_bytes("attachments/a/b", b"y", role=FileRole.ATTACHMENT, media_type="a/b")
    with pytest.raises(PackageLayoutError):
        builder.add_evidence("other/x", b"y")


def test_builder_enforces_size_limit(tmp_path: Path) -> None:
    builder, build_dir = _builder(tmp_path, max_package_bytes=10)
    builder.add_bytes("attachments/a", b"123456", role=FileRole.ATTACHMENT, media_type="a/b")
    with pytest.raises(PackageTooLargeError):
        builder.add_bytes("attachments/b", b"12345", role=FileRole.ATTACHMENT, media_type="a/b")
    with pytest.raises(PackageTooLargeError):
        builder.add_evidence("evidence/x", b"12345")
    assert not (build_dir / "attachments" / "b").exists()
    builder.add_bytes("attachments/c", b"1234", role=FileRole.ATTACHMENT, media_type="a/b")
    assert builder.total_bytes == 10


def test_write_manifest_lists_files_but_not_evidence(tmp_path: Path) -> None:
    build_dir, manifest = _populated(tmp_path)
    assert [file.path for file in manifest.files] == ["capture.json", "source/ticket.json"]
    assert (build_dir / "manifest.json").is_file()
    assert (build_dir / "evidence/validation/01-cert.der").read_bytes() == b"der"
    assert verify_package_files(build_dir, manifest) == []


def test_verify_detects_missing_changed_resized_and_unlisted(tmp_path: Path) -> None:
    build_dir, manifest = _populated(tmp_path)
    (build_dir / "capture.json").unlink()
    (build_dir / "source" / "ticket.json").write_bytes(b"TICKET")
    (build_dir / "extra.txt").write_bytes(b"x")
    assert verify_package_files(build_dir, manifest) == [
        FileProblem("capture.json", "missing"),
        FileProblem("extra.txt", "unlisted"),
        FileProblem("source/ticket.json", "sha256"),
    ]
    (build_dir / "source" / "ticket.json").write_bytes(b"longer ticket")
    assert FileProblem("source/ticket.json", "size") in verify_package_files(build_dir, manifest)


def test_verify_ignores_reserved_and_evidence_files(tmp_path: Path) -> None:
    build_dir, manifest = _populated(tmp_path)
    (build_dir / "manifest.json.p7s").write_bytes(b"seal")
    (build_dir / "manifest.json.p7s.tst").write_bytes(b"token")
    (build_dir / "evidence" / "renewal-0001.tst").write_bytes(b"t")
    assert verify_package_files(build_dir, manifest) == []


def test_verify_reports_symlinks_without_following_them(tmp_path: Path) -> None:
    build_dir, manifest = _populated(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"secret")
    try:
        (build_dir / "linked").symlink_to(outside, target_is_directory=True)
        (build_dir / "capture.json").unlink()
        (build_dir / "capture.json").symlink_to(outside / "secret.txt")
    except OSError:
        pytest.skip("symlinks are not supported in this environment")
    assert verify_package_files(build_dir, manifest) == [
        FileProblem("capture.json", "missing"),
        FileProblem("capture.json", "unlisted"),
        FileProblem("linked", "unlisted"),
    ]


def test_verify_reports_fifos_as_unlisted_or_missing(tmp_path: Path) -> None:
    build_dir, manifest = _populated(tmp_path)
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs are not supported on this platform")
    os.mkfifo(build_dir / "pipe")
    (build_dir / "capture.json").unlink()
    os.mkfifo(build_dir / "capture.json")
    os.mkfifo(build_dir / "evidence" / "hidden-pipe")
    assert verify_package_files(build_dir, manifest) == [
        FileProblem("capture.json", "missing"),
        FileProblem("capture.json", "unlisted"),
        FileProblem("evidence/hidden-pipe", "unlisted"),
        FileProblem("pipe", "unlisted"),
    ]


@pytest.mark.parametrize(
    ("path", "role"),
    [
        ("reading-copy-ua.pdf", FileRole.READING_COPY_ARCHIVAL),
        ("source/ticket.json", FileRole.SOURCE_ARTICLES),
        ("source/articles/101.txt", FileRole.ORIGINAL_EMAIL),
        ("x.bin", FileRole.ATTACHMENT),
        ("attachments/01-dsn", FileRole.MAIL_EVIDENCE),
    ],
)
def test_builder_enforces_role_paths(tmp_path: Path, path: str, role: FileRole) -> None:
    builder, build_dir = _builder(tmp_path)
    source = tmp_path / "src.bin"
    source.write_bytes(b"content")
    with pytest.raises(PackageLayoutError, match=str(role)):
        builder.add_bytes(path, b"x", role=role, media_type="a/b")
    with pytest.raises(PackageLayoutError, match=str(role)):
        builder.add_file(path, source, role=role, media_type="a/b")
    assert list(build_dir.iterdir()) == []
    assert builder.total_bytes == 0


def test_add_file_refuses_a_source_that_changes_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder, build_dir = _builder(tmp_path)
    source = tmp_path / "src.bin"
    source.write_bytes(b"content")
    original = package_module._copy_and_hash

    def grow_then_copy(src: Path, target: Path) -> tuple[int, str]:
        with src.open("ab") as handle:
            handle.write(b" and more")
        return original(src, target)

    monkeypatch.setattr(package_module, "_copy_and_hash", grow_then_copy)
    with pytest.raises(PackageLayoutError, match="changed size"):
        builder.add_file("attachments/x.bin", source, role=FileRole.ATTACHMENT, media_type="a/b")
    assert builder.files() == ()
    assert not (build_dir / "attachments" / "x.bin").exists()


def _write_manifest_with(builder: PackageBuilder, *, policy_id: str, coverage: Coverage) -> bytes:
    """Write a manifest with the fixed sample metadata but the given policy and coverage."""
    return builder.write_manifest(
        revision_id="rid",
        revision_number=1,
        previous_revision_id=None,
        ticket=TicketRef(1, "10001"),
        captured_at=datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC),
        record_class="pilot-test",
        policy_id=policy_id,
        source_digest="sha256:" + "2" * 64,
        coverage=coverage,
        producer=Producer("chronikwerk", "0"),
    )


def test_write_manifest_refuses_an_invalid_manifest(tmp_path: Path) -> None:
    builder, build_dir = _builder(tmp_path)
    builder.add_bytes("capture.json", b"{}", role=FileRole.CAPTURE_DESCRIPTION, media_type="x/y")
    gaps = Coverage(CoverageCount(2, 1), CoverageCount(0, 0), OriginalEmailCoverage(0, 0), True)
    full = Coverage(CoverageCount(1, 1), CoverageCount(0, 0), OriginalEmailCoverage(0, 0), True)
    with pytest.raises(ManifestError, match="complete"):
        _write_manifest_with(builder, policy_id="sha256:" + "1" * 64, coverage=gaps)
    with pytest.raises(ManifestError, match="policy_id"):
        _write_manifest_with(builder, policy_id="sha256:p", coverage=full)
    assert not (build_dir / "manifest.json").exists()
