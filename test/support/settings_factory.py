from __future__ import annotations

from copy import deepcopy
from typing import Any

from zammad_pdf_archiver.config.settings import Settings


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
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
    allow_unsigned: bool = True,
    allow_unsigned_when_no_secret: bool = True,
    require_delivery_id: bool = False,
    overrides: dict[str, Any] | None = None,
) -> Settings:
    data: dict[str, Any] = {
        "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
        "storage": {"root": storage_root},
        "hardening": {
            "webhook": {
                "allow_unsigned": allow_unsigned,
                "allow_unsigned_when_no_secret": allow_unsigned_when_no_secret,
                "require_delivery_id": require_delivery_id,
            }
        },
    }
    if secret is not None:
        data["zammad"]["webhook_hmac_secret"] = secret
    if overrides:
        data = _deep_merge(data, overrides)
    return Settings.from_mapping(data)

