"""Verify the local-path startup checks, the clean startup refusal and the housekeeping sweep."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from chronikwerk import composition, runtime
from chronikwerk.composition_preservation import (
    _housekeeping,
    abandoned_capture_dirs,
    startup_problems,
    sweep_capture_temp,
)
from chronikwerk.configuration.validation import ConfigValidationError, ConfigValidationIssue
from chronikwerk.storage.archive_fs import FilesystemArchiveStore
from tests.support.preservation_settings import make_runtime_settings

# The housekeeping callback reads the real clock, so the fixtures are relative to it.
NOW = datetime.now(UTC)
_OLD = (NOW - timedelta(days=2)).timestamp()

needs_non_root = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permission bits")


def _paths(settings: Any) -> set[str]:
    """Return the setting paths ``startup_problems`` names."""
    return {issue.path for issue in startup_problems(settings)}


@contextmanager
def _mode(path: Path, mode: int) -> Iterator[None]:
    """Temporarily change the mode of a ``tmp_path`` entry."""
    original = path.stat().st_mode
    path.chmod(mode)
    try:
        yield
    finally:
        path.chmod(original)


def test_valid_runtime_settings_have_no_problems(tmp_path: Path) -> None:
    assert startup_problems(make_runtime_settings(tmp_path)) == []


def test_journal_directory_that_resolves_into_the_archive_root_is_refused(tmp_path: Path) -> None:
    (tmp_path / "archive" / "journal").mkdir(parents=True)
    (tmp_path / "journal-link").symlink_to(tmp_path / "archive" / "journal")
    settings = make_runtime_settings(
        tmp_path, overrides={"journal": {"path": str(tmp_path / "journal-link" / "j.sqlite3")}}
    )

    [issue] = startup_problems(settings)

    assert issue.path == "journal.path"
    assert "not below storage.root" in issue.message


def test_build_area_symlink_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real-tmp"
    real.mkdir(mode=0o700)
    (tmp_path / "tmp-link").symlink_to(real)
    settings = make_runtime_settings(
        tmp_path, overrides={"capture": {"temp_dir": str(tmp_path / "tmp-link")}}
    )

    [issue] = startup_problems(settings)

    assert issue.path == "capture.temp_dir"
    assert "symbolic link" in issue.message


def test_build_area_symlinked_into_the_archive_root_is_refused(tmp_path: Path) -> None:
    (tmp_path / "archive" / "tmp").mkdir(parents=True)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "link").symlink_to(tmp_path / "archive")
    settings = make_runtime_settings(
        tmp_path, overrides={"capture": {"temp_dir": str(tmp_path / "state" / "link" / "tmp")}}
    )

    messages = [issue.message for issue in startup_problems(settings)]

    assert "must be on local disk, not below storage.root" in messages


def test_build_area_with_group_access_is_refused(tmp_path: Path) -> None:
    temp_dir = tmp_path / "state" / "tmp"
    temp_dir.mkdir(parents=True)
    temp_dir.chmod(0o750)

    [issue] = startup_problems(make_runtime_settings(tmp_path))

    assert issue.path == "capture.temp_dir"
    assert "group or other access" in issue.message


def test_build_area_owned_by_another_user_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_runtime_settings(tmp_path)
    settings.capture.temp_dir.mkdir(mode=0o700)
    owner = settings.capture.temp_dir.stat().st_uid
    monkeypatch.setattr(os, "geteuid", lambda: owner + 1)

    [issue] = startup_problems(settings)

    assert issue.path == "capture.temp_dir"
    assert "owned by the service user" in issue.message


def test_missing_or_non_directory_archive_root_is_refused(tmp_path: Path) -> None:
    settings = make_runtime_settings(tmp_path)
    (tmp_path / "archive").rmdir()
    assert _paths(settings) == {"storage.root"}

    (tmp_path / "archive").write_text("not a directory", encoding="utf-8")
    assert _paths(settings) == {"storage.root"}


@needs_non_root
def test_read_only_archive_root_and_journal_directory_are_refused(tmp_path: Path) -> None:
    settings = make_runtime_settings(tmp_path)
    with _mode(tmp_path / "archive", 0o500), _mode(tmp_path / "state", 0o500):
        problems = startup_problems(settings)

    assert {(issue.path, "not writable" in issue.message) for issue in problems} == {
        ("storage.root", True),
        ("journal.path", True),
    }


@needs_non_root
def test_unreadable_preservation_files_are_reported_as_problems(tmp_path: Path) -> None:
    settings = make_runtime_settings(tmp_path)
    assert settings.verify.trust_anchors_path is not None
    assert settings.seal.pfx_path is not None
    with (
        _mode(settings.verify.trust_anchors_path, 0),
        _mode(settings.seal.pfx_path, 0),
        _mode(settings.retention.policy_path, 0),
    ):
        paths = _paths(settings)

    assert paths == {"verify.trust_anchors_path", "seal.pfx_path", "retention.policy_path"}


def test_runtime_main_prints_every_problem_and_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    issues = [
        ConfigValidationIssue(path="journal.path", message="directory /x does not exist"),
        ConfigValidationIssue(path="storage.root", message="directory /y does not exist"),
    ]

    def refuse() -> None:
        raise ConfigValidationError(issues)

    monkeypatch.setattr(composition, "build_runtime_application", refuse)

    with pytest.raises(SystemExit) as raised:
        runtime.main()

    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "- journal.path: directory /x does not exist" in err
    assert "- storage.root: directory /y does not exist" in err


def _capture_tree(temp_dir: Path) -> dict[str, Path]:
    """Create sweep candidates and decoys below ``temp_dir`` (all inside ``tmp_path``)."""
    temp_dir.mkdir(mode=0o700)
    outside = temp_dir.parent / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    entries = {
        "old": temp_dir / "chronikwerk-capture-abc_123",
        "fresh": temp_dir / "chronikwerk-capture-fresh1",
        "odd_name": temp_dir / "chronikwerk-capture-a.b",
        "foreign": temp_dir / "other-old-dir",
    }
    for path in entries.values():
        path.mkdir()
        (path / "payload.bin").write_bytes(b"x")
    link = temp_dir / "chronikwerk-capture-link"
    link.symlink_to(outside)
    old_file = temp_dir / "chronikwerk-capture-file"
    old_file.write_text("file", encoding="utf-8")
    for name in ("old", "odd_name", "foreign"):
        os.utime(entries[name], (_OLD, _OLD))
    os.utime(link, (_OLD, _OLD), follow_symlinks=False)
    os.utime(old_file, (_OLD, _OLD))
    entries["outside"] = outside
    entries["link"] = link
    entries["file"] = old_file
    return entries


def test_sweep_selects_only_old_real_capture_directories(tmp_path: Path) -> None:
    temp_dir = tmp_path / "tmp"
    entries = _capture_tree(temp_dir)

    found = abandoned_capture_dirs(temp_dir, cutoff=(NOW - timedelta(days=1)).timestamp())

    assert found == [entries["old"]]
    assert abandoned_capture_dirs(tmp_path / "missing", cutoff=NOW.timestamp()) == []


def test_sweep_removes_through_the_root_confined_helper(tmp_path: Path) -> None:
    temp_dir = tmp_path / "tmp"
    entries = _capture_tree(temp_dir)
    calls: list[tuple[Path, Path]] = []

    def remove(path: Path, *, storage_root: Path, **_options: object) -> None:
        calls.append((path, storage_root))

    assert sweep_capture_temp(temp_dir, now=NOW, remove=remove) == 1
    assert calls == [(entries["old"], temp_dir)]


def test_sweep_deletes_the_abandoned_directory_and_keeps_everything_else(tmp_path: Path) -> None:
    temp_dir = tmp_path / "tmp"
    entries = _capture_tree(temp_dir)

    assert sweep_capture_temp(temp_dir, now=NOW) == 1

    assert not entries["old"].exists()
    for name in ("fresh", "odd_name", "foreign", "link", "file"):
        assert os.path.lexists(entries[name]), name
    assert (entries["outside"] / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_housekeeping_sweeps_the_build_area_even_when_staging_cleanup_fails(
    tmp_path: Path,
) -> None:
    temp_dir = tmp_path / "tmp"
    entries = _capture_tree(temp_dir)
    seen: list[timedelta] = []

    class Store:
        def cleanup_abandoned_staging(self, *, older_than: timedelta, now: datetime) -> int:
            seen.append(older_than)
            raise OSError("staging root unavailable")

    callback = _housekeeping(cast(FilesystemArchiveStore, Store()), temp_dir)

    with pytest.raises(OSError, match="staging root unavailable"):
        asyncio.run(callback())

    assert seen == [timedelta(days=1)]
    assert not entries["old"].exists()
