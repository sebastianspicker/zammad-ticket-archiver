#!/usr/bin/env python3
"""Assemble modular admin CSS into the shipped static stylesheet.

Source of truth: frontend/admin/css/*.css (sorted by filename).
Default output:   src/chronikwerk/web/static/admin/admin.css

Usage:
  python scripts/ci/assemble_admin_css.py
  python scripts/ci/assemble_admin_css.py -o build/admin/admin.css
  python scripts/ci/assemble_admin_css.py --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CSS_SRC = REPO_ROOT / "frontend" / "admin" / "css"
DEFAULT_OUT = REPO_ROOT / "src" / "chronikwerk" / "web" / "static" / "admin" / "admin.css"


def collect_sources() -> list[Path]:
    """Return the ordered CSS source files."""
    if not CSS_SRC.is_dir():
        raise SystemExit(f"CSS source directory missing: {CSS_SRC}")
    files = sorted(p for p in CSS_SRC.glob("*.css") if p.is_file())
    if not files:
        raise SystemExit(f"No .css files found in {CSS_SRC}")
    return files


def assemble(sources: list[Path]) -> str:
    """Combine CSS sources into the shipped stylesheet."""
    parts: list[str] = [
        "/* Assembled by scripts/ci/assemble_admin_css.py; do not edit directly. */\n",
        f"/* Sources: frontend/admin/css/*.css ({len(sources)} files) */\n\n",
    ]
    for path in sources:
        rel = path.relative_to(REPO_ROOT).as_posix()
        parts.append(f"/* ===== {rel} ===== */\n")
        text = path.read_text(encoding="utf-8")
        parts.append(text.rstrip() + "\n\n")
    return "".join(parts)


def main(argv: list[str] | None = None) -> int:
    """Assemble the stylesheet or verify that the shipped copy is current."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output path (default: {DEFAULT_OUT.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if output differs from assembled sources (no write).",
    )
    args = parser.parse_args(argv)

    sources = collect_sources()
    css = assemble(sources)
    out: Path = args.output
    if not out.is_absolute():
        out = REPO_ROOT / out

    if args.check:
        existing = out.read_text(encoding="utf-8") if out.is_file() else ""
        if existing != css:
            print(
                f"{out.relative_to(REPO_ROOT)} is stale; "
                "run 'make frontend-update' or "
                "'python scripts/ci/assemble_admin_css.py'.",
                file=sys.stderr,
            )
            return 1
        print(f"{out.relative_to(REPO_ROOT)} is up to date ({len(sources)} sources).")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(css, encoding="utf-8")
    try:
        rel = out.relative_to(REPO_ROOT)
    except ValueError:
        rel = out
    print(f"Wrote {rel} ({len(css)} bytes from {len(sources)} files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
