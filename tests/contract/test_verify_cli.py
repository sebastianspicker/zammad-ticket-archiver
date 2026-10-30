"""Pin the offline `chronikwerk-admin verify` contract: exit codes, report, no configuration."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes

from chronikwerk.cli import main
from tests.support.pki import build_pki
from tests.support.sealed_package import build_sealed_package, write_anchors


def _run(monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    """Invoke the CLI entry point with the given argument vector."""
    monkeypatch.setattr(sys, "argv", ["chronikwerk-admin", *args])
    return main()


@pytest.fixture
def empty_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Remove every Chronikwerk, Zammad and storage variable and point CONFIG_PATH nowhere."""
    prefixes = ("CHRONIKWERK", "ZAMMAD", "STORAGE", "WORKFLOW__")
    for name in [key for key in os.environ if key.startswith(prefixes)]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "does-not-exist.yaml"))


def test_intact_package_exits_zero_without_any_configuration(
    empty_environment: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = build_sealed_package(tmp_path)
    anchors = write_anchors(tmp_path)

    code = _run(monkeypatch, "verify", "--path", str(package), "--trust-anchors", str(anchors))

    out = capsys.readouterr().out
    assert code == 0
    assert "Revision: rev-1" in out
    assert "[ok] seal:" in out
    assert out.rstrip().endswith("Result: PASSED")


def test_tampered_package_exits_one(
    empty_environment: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = build_sealed_package(tmp_path)
    anchors = write_anchors(tmp_path)
    (package / "capture.json").write_bytes(b"tampered")

    code = _run(monkeypatch, "verify", "--path", str(package), "--trust-anchors", str(anchors))

    out = capsys.readouterr().out
    assert code == 1
    assert "[FAIL] files: capture.json (size)" in out
    assert out.rstrip().endswith("Result: FAILED")


def test_copied_package_verifies_from_another_location(
    empty_environment: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package = build_sealed_package(tmp_path)
    anchors = write_anchors(tmp_path)
    elsewhere = tmp_path / "restore" / "copy"
    shutil.copytree(package, elsewhere)

    code = _run(monkeypatch, "verify", "--path", str(elsewhere), "--trust-anchors", str(anchors))

    assert code == 0


def test_missing_directory_is_a_usage_error(
    empty_environment: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    anchors = write_anchors(tmp_path)

    code = _run(
        monkeypatch, "verify", "--path", str(tmp_path / "nope"), "--trust-anchors", str(anchors)
    )

    assert code == 2
    assert "Package directory not found" in capsys.readouterr().err


def test_unreadable_anchors_are_a_usage_error(
    empty_environment: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = build_sealed_package(tmp_path)
    garbage = tmp_path / "garbage.pem"
    garbage.write_bytes(b"not pem")

    code = _run(monkeypatch, "verify", "--path", str(package), "--trust-anchors", str(garbage))
    missing = _run(
        monkeypatch,
        "verify",
        "--path",
        str(package),
        "--trust-anchors",
        str(tmp_path / "missing.pem"),
    )

    assert (code, missing) == (2, 2)
    assert "Trust anchor bundle" in capsys.readouterr().err


def test_report_is_written_and_never_overwritten(
    empty_environment: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = build_sealed_package(tmp_path)
    anchors = write_anchors(tmp_path)
    report = tmp_path / "report.json"
    args = ("verify", "--path", str(package), "--trust-anchors", str(anchors))

    first = _run(monkeypatch, *args, "--report", str(report))
    written = report.read_bytes()
    second = _run(monkeypatch, *args, "--report", str(report))

    assert first == 0
    assert json.loads(written)["result"] == "passed"
    assert second == 2
    assert report.read_bytes() == written
    assert "already exists" in capsys.readouterr().err


def test_failed_verification_still_writes_a_failed_report(
    empty_environment: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package = build_sealed_package(tmp_path)
    anchors = write_anchors(tmp_path, trusted=False)
    report = tmp_path / "report.json"

    code = _run(
        monkeypatch,
        "verify",
        "--path",
        str(package),
        "--trust-anchors",
        str(anchors),
        "--report",
        str(report),
    )

    document = json.loads(report.read_bytes())
    assert code == 1
    assert document["result"] == "failed"
    assert {c["name"] for c in document["checks"] if not c["ok"]} == {"seal", "timestamp"}


def test_expected_signer_pins_the_seal_certificate(
    empty_environment: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = build_sealed_package(tmp_path)
    anchors = write_anchors(tmp_path)
    fingerprint = build_pki().seal.certificate.fingerprint(hashes.SHA256()).hex()
    args = ("verify", "--path", str(package), "--trust-anchors", str(anchors))

    pinned = _run(
        monkeypatch, *args, "--expected-signer", "0" * 64, "--expected-signer", fingerprint
    )
    pinned_out = capsys.readouterr().out
    mismatched = _run(monkeypatch, *args, "--expected-signer", "0" * 64)
    mismatched_out = capsys.readouterr().out
    malformed = _run(monkeypatch, *args, "--expected-signer", "not-a-fingerprint")

    assert pinned == 0
    assert "revocation not checked" in pinned_out
    assert mismatched == 1
    assert "[FAIL] seal:" in mismatched_out
    assert malformed == 2
    assert "--expected-signer" in capsys.readouterr().err


def test_missing_required_arguments_exit_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        _run(monkeypatch, "verify", "--path", "somewhere")

    assert raised.value.code == 2
    assert "--trust-anchors" in capsys.readouterr().err
