#!/usr/bin/env python3
"""Reject stale pre-Chronikwerk identity outside the migration guide."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (
    Path(".github"),
    Path("config"),
    Path("docs"),
    Path("frontend"),
    Path("infra"),
    Path("scripts"),
    Path("src"),
    Path("tests"),
)
SCAN_FILES = (
    Path(".dockerignore"),
    Path(".github/CODEOWNERS"),
    Path(".gitignore"),
    Path("CHANGELOG.md"),
    Path("CONTRIBUTING.md"),
    Path("DESIGN.md"),
    Path("Dockerfile"),
    Path("Dockerfile.dev"),
    Path("Makefile"),
    Path("PRODUCT.md"),
    Path("README.md"),
    Path("RELEASE_STATUS.md"),
    Path("SECURITY.md"),
    Path("docker-compose.dev.yml"),
    Path("docker-compose.yml"),
    Path("package.json"),
    Path("pyproject.toml"),
    Path("tsconfig.json"),
)
TEXT_SUFFIXES = {
    ".css",
    ".env",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".service",
    ".svg",
    ".toml",
    ".ts",
    ".txt",
    ".yaml",
    ".yml",
}
ALLOWLIST = {Path("docs/migration-to-chronikwerk.md")}
STALE_IDENTITY = re.compile(
    r"zammad(?:[_ -]+(?:pdf|ticket))?[_ -]+archiver",
    re.IGNORECASE,
)


def _candidate_files() -> list[Path]:
    """Return maintained text surfaces without traversing environment files."""
    candidates = {path for path in SCAN_FILES if (REPO_ROOT / path).is_file()}
    for root in SCAN_ROOTS:
        absolute_root = REPO_ROOT / root
        if not absolute_root.exists():
            continue
        candidates.update(
            path.relative_to(REPO_ROOT)
            for path in absolute_root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in TEXT_SUFFIXES
            and not path.name.startswith(".env")
        )
    return sorted(candidates - ALLOWLIST)


def _errors() -> list[str]:
    """List every stale identity with exact repository path and line number."""
    errors: list[str] = []
    for relative in _candidate_files():
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        errors.extend(
            f"{relative}:{line_number}: stale identity: {match.group(0)}"
            for line_number, line in enumerate(text.splitlines(), start=1)
            if (match := STALE_IDENTITY.search(line)) is not None
        )
    return errors


def main() -> int:
    """Print an actionable report and return non-zero when stale identity remains."""
    errors = _errors()
    if errors:
        print("\n".join(errors))
        return 1
    print("brand-identity-check: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
