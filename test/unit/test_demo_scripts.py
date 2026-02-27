from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run_script(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_seed_demo_data_supports_dry_run() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "demo" / "seed_demo_data.py"

    proc = _run_script(script, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "POST /__demo/reset" in proc.stdout
    assert "POST /ingest" in proc.stdout
    assert "demo-seed-report.json" in proc.stdout


def test_capture_screenshots_supports_dry_run() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "demo" / "capture_screenshots.py"

    proc = _run_script(script, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "01-admin-token-screen.png" in proc.stdout
    assert "09-api-503-backend-unavailable.png" in proc.stdout
    assert "docker compose -f docker-compose.demo.yml stop redis-demo" in proc.stdout

