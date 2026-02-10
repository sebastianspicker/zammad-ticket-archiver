"""NFR9: Support Python 3.12+; declared dependencies."""
from __future__ import annotations

from pathlib import Path


def test_nfr9_pyproject_requires_python_312_plus() -> None:
    """NFR9: pyproject.toml must require Python >=3.12."""
    repo_root = Path(__file__).resolve().parents[2]
    pyproject = repo_root / "pyproject.toml"
    assert pyproject.is_file()
    text = pyproject.read_text()
    # Parse [project] requires-python
    in_project = False
    for line in text.splitlines():
        if line.strip() == "[project]":
            in_project = True
            continue
        if in_project and line.startswith("requires-python"):
            value = line.split("=", 1)[1].strip().strip('"\'')
            assert "3.12" in value or "3.13" in value, (
                f"requires-python should be >=3.12, got {value}"
            )
            return
        if in_project and line.startswith("["):
            break
    raise AssertionError("requires-python not found in pyproject.toml [project]")
