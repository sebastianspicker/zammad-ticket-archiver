"""NFR7: Single process; Docker and systemd deployment support."""
from __future__ import annotations

from pathlib import Path

from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def test_nfr7_app_creates_with_settings(tmp_path: Path) -> None:
    """NFR7: create_app must run with settings (single-process entry)."""
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "tok"},
            "storage": {"root": str(tmp_path)},
        }
    )
    app = create_app(settings)
    assert app.state.settings is settings


def test_nfr7_dockerfile_exists() -> None:
    """NFR7: Dockerfile must exist for container deployment."""
    repo_root = Path(__file__).resolve().parents[2]
    dockerfile = repo_root / "Dockerfile"
    assert dockerfile.is_file(), "Dockerfile required for deployment"
    content = dockerfile.read_text()
    assert "python" in content.lower() or "uvicorn" in content.lower()
