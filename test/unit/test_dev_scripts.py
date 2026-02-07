from __future__ import annotations

import subprocess
from pathlib import Path


def test_dev_run_local_script_is_not_placeholder() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "dev" / "run-local.sh"

    proc = subprocess.run([str(script), "--dry-run"], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "TODO" not in proc.stdout
    assert "uvicorn" in proc.stdout
    assert "zammad_pdf_archiver.asgi:app" in proc.stdout


def test_dev_gen_certs_script_is_not_placeholder(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "dev" / "gen-dev-certs.sh"

    out_dir = tmp_path / "certs"
    proc = subprocess.run(
        [str(script), "--dry-run", str(out_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "TODO" not in proc.stdout
    assert "openssl" in proc.stdout
