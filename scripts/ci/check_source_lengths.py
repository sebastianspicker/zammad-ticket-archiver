#!/usr/bin/env python3
"""Enforce the physical-line limit for repository-maintained source files."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_LINES = 600
MAINTAINED_ROOTS = (
    "src/chronikwerk",
    "scripts",
    "tests",
    "frontend",
)
ROOT_SOURCES: tuple[str, ...] = ()
SOURCE_SUFFIXES = {".css", ".html", ".js", ".mjs", ".py", ".sh", ".ts"}
GENERATED_EXEMPTIONS = {
    "src/chronikwerk/web/static/admin/admin.css",
    "src/chronikwerk/web/static/admin/admin.js",
}


def maintained_source_paths(repo_root: Path) -> list[Path]:
    """Return maintained source paths in deterministic repository order."""
    paths: set[Path] = set()
    for relative_root in MAINTAINED_ROOTS:
        root = repo_root / relative_root
        if not root.exists():
            continue
        paths.update(
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix in SOURCE_SUFFIXES
            and path.relative_to(repo_root).as_posix() not in GENERATED_EXEMPTIONS
        )

    for relative_path in ROOT_SOURCES:
        path = repo_root / relative_path
        if path.is_file():
            paths.add(path)

    return sorted(paths, key=lambda path: path.relative_to(repo_root).as_posix())


def physical_line_count(data: bytes) -> int:
    """Count newline-delimited physical lines, including a final unterminated line."""
    if not data:
        return 0
    return data.count(b"\n") + (not data.endswith(b"\n"))


def scan_source_lengths(
    repo_root: Path,
) -> tuple[list[tuple[str, int]], list[tuple[str, str]], int]:
    """Return length offenders, unreadable files, and the number of scanned files."""
    offenders: list[tuple[str, int]] = []
    failures: list[tuple[str, str]] = []
    paths = maintained_source_paths(repo_root)
    for path in paths:
        relative_path = path.relative_to(repo_root).as_posix()
        try:
            data = path.read_bytes()
            data.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            failures.append((relative_path, str(exc)))
            continue
        line_count = physical_line_count(data)
        if line_count > MAX_LINES:
            offenders.append((relative_path, line_count))
    return offenders, failures, len(paths)


def main(repo_root: Path = REPO_ROOT) -> int:
    """Report every source-length violation and fail closed on unreadable input."""
    try:
        offenders, failures, scanned_count = scan_source_lengths(repo_root)
    except OSError as exc:
        print(f"source-length-check: unable to discover sources: {exc}", file=sys.stderr)
        return 1

    for relative_path, line_count in offenders:
        print(
            f"source-length-check: {relative_path}: {line_count} lines (maximum {MAX_LINES})",
            file=sys.stderr,
        )
    for relative_path, detail in failures:
        print(
            f"source-length-check: {relative_path}: unreadable source: {detail}",
            file=sys.stderr,
        )
    if offenders or failures:
        return 1

    print(f"source-length-check: OK ({scanned_count} files, maximum {MAX_LINES} lines).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
