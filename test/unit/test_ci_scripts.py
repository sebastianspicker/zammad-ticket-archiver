from __future__ import annotations

import subprocess
from pathlib import Path


def test_ci_smoke_script_checks_current_repo_layout() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "ci" / "smoke-test.sh"

    proc = subprocess.run([str(script)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "Missing required path" not in proc.stderr
    assert "OK." in proc.stdout


def test_makefile_qa_target_runs_smoke_test() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    assert "scripts/ci/smoke-test.sh" in makefile
