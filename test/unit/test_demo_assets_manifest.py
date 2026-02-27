from __future__ import annotations

import json
from pathlib import Path


def test_demo_screenshot_manifest_contains_expected_extended_set() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    manifest_path = repo_root / "docs" / "assets" / "demo" / "screenshot-manifest.json"

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = [
        "01-admin-token-screen.png",
        "02-admin-queue-stats.png",
        "03-admin-history-all.png",
        "04-admin-history-filtered-ticket.png",
        "05-admin-retry-action.png",
        "06-admin-dlq-before-drain.png",
        "07-admin-dlq-after-drain.png",
        "08-api-401-unauthorized.png",
        "09-api-503-backend-unavailable.png",
        "10-admin-mobile-viewport.png",
    ]
    assert payload == expected

