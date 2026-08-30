"""Builds deterministic, valid settings with recursive overrides for isolated tests."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from chronikwerk.configuration.models import Settings


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge nested overrides without mutating the valid baseline."""
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def make_settings(
    storage_root: str,
    *,
    secret: str | None = None,
    require_delivery_id: bool = False,
    overrides: dict[str, Any] | None = None,
) -> Settings:
    """Build valid baseline settings, then apply only the scenario-specific overrides."""
    data: dict[str, Any] = {
        "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
        "storage": {"root": storage_root},
        "hardening": {
            "webhook": {
                "require_delivery_id": require_delivery_id,
            },
            # Test fixtures use non-resolvable example hosts and opt into the
            # explicit internal-network override.
            "transport": {"allow_private_networks": True},
        },
    }
    if secret is not None:
        data["zammad"]["webhook_hmac_secret"] = secret
    if overrides:
        data = _deep_merge(data, overrides)
    return Settings.from_mapping(data)


def write_test_config(
    config_path: Path,
    storage_root: Path,
    *,
    state_dir: Path | None = None,
) -> None:
    """Write the shared minimal YAML configuration used by CLI/contract tests."""
    lines = [
        "zammad:",
        "  base_url: https://zammad.example.local",
        "  api_token: test-token",
        "  webhook_hmac_secret: test-webhook-hmac-secret-0123456789abcdef",
        "storage:",
        f"  root: {storage_root}",
    ]
    if state_dir is not None:
        lines.extend(("admin:", f"  state_dir: {state_dir}"))
    lines.append("")
    config_path.write_text("\n".join(lines), encoding="utf-8")
