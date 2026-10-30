"""Build settings that meet the preservation startup requirements below one test directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from chronikwerk.configuration.models import Settings
from tests.support.pki import PFX_PASSWORD
from tests.support.sealed_package import seal_options, write_anchors
from tests.support.settings_factory import _deep_merge, make_settings, write_test_config
from tests.support.tsa import TSA_URL

RECORD_CLASS = "pilot-test"
_POLICY = f"""record_classes:
  {RECORD_CLASS}:
    retention: P30D
    trigger: capture
    readers: [archive-readers]
"""


def preservation_overrides(base: Path) -> dict[str, Any]:
    """Write a seal PFX, trust anchors and a one-class policy below ``base``; return settings.

    The journal and the build area live in ``base / "state"``, outside any archive root.
    """
    keys = base / "keys"
    state = base / "state"
    keys.mkdir(parents=True, exist_ok=True)
    state.mkdir(exist_ok=True)
    seal = seal_options(keys)
    policy = keys / "retention.yaml"
    policy.write_text(_POLICY, encoding="utf-8")
    return {
        "journal": {"path": str(state / "journal.sqlite3")},
        "capture": {"temp_dir": str(state / "tmp")},
        "seal": {"pfx_path": str(seal.pfx_path), "pfx_password": PFX_PASSWORD},
        "verify": {"trust_anchors_path": str(write_anchors(keys))},
        "retention": {"policy_path": str(policy)},
        "signing": {
            "enabled": True,
            "pfx_path": str(seal.pfx_path),
            "pfx_password": PFX_PASSWORD,
            "timestamp": {"enabled": True, "rfc3161": {"tsa_url": TSA_URL}},
        },
    }


def make_runtime_settings(
    base: Path,
    *,
    secret: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> Settings:
    """Return valid settings with the archive root at ``base / "archive"`` and preservation set."""
    archive = base / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    merged = _deep_merge(preservation_overrides(base), overrides or {})
    return make_settings(str(archive), secret=secret, overrides=merged)


def write_runtime_config(config_path: Path, base: Path, *, state_dir: Path | None = None) -> None:
    """Write the shared minimal YAML configuration plus the preservation settings."""
    archive = base / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    write_test_config(config_path, archive, state_dir=state_dir)
    with config_path.open("a", encoding="utf-8") as handle:
        yaml.safe_dump(preservation_overrides(base), handle, sort_keys=True)
