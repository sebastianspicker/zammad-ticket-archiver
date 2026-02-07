from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_mypy_clean(tmp_path: Path) -> None:
    if importlib.util.find_spec("mypy") is None:
        pytest.skip("mypy is not installed in this environment")

    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["MYPY_CACHE_DIR"] = str(tmp_path)

    proc = subprocess.run(
        [sys.executable, "-m", "mypy", ".", "--config-file", "pyproject.toml"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (proc.stdout + proc.stderr)

