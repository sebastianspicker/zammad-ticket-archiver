"""Verify the portable Zammad connection contract at public boundaries."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import SecretStr

from chronikwerk.configuration.load import load_settings
from chronikwerk.configuration.models import (
    ZAMMAD_CONNECTION_CONTRACT_VERSION,
    Settings,
    ZammadConnection,
)
from chronikwerk.configuration.validation import ConfigValidationError, validate_settings
from chronikwerk.zammad.gateway import AsyncZammadClient

_WEBHOOK_SECRET = "test-webhook-secret-0123456789abcdef"
_CONNECTION_ENV = (
    "ZAMMAD_ORIGIN",
    "ZAMMAD_API_TOKEN",
    "ZAMMAD_TIMEOUT_SECONDS",
    "ZAMMAD_ALLOW_PRIVATE_ORIGIN",
    "ZAMMAD__BASE_URL",
    "ZAMMAD__API_TOKEN",
    "ZAMMAD__TIMEOUT_SECONDS",
)


@pytest.fixture(autouse=True)
def clear_connection_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove ambient Zammad variables so each contract case is deterministic."""
    for key in _CONNECTION_ENV:
        monkeypatch.delenv(key, raising=False)


def _yaml(tmp_path: Path) -> Path:
    """Write the minimal valid YAML base used by environment-precedence cases."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "\n".join(
            (
                "zammad:",
                "  base_url: https://zammad.from-yaml.example",
                "  api_token: yaml-token",
                f"  webhook_hmac_secret: {_WEBHOOK_SECRET}",
                "storage:",
                f"  root: {tmp_path}",
                "",
            )
        ),
        encoding="utf-8",
    )
    return path


def test_contract_version_and_environment_override_are_explicit(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ZAMMAD_ORIGIN", "https://Zammad.From-Env.example/")
    monkeypatch.setenv("ZAMMAD_API_TOKEN", "canonical-token")
    monkeypatch.setenv("ZAMMAD_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("ZAMMAD_ALLOW_PRIVATE_ORIGIN", "true")

    settings = load_settings(config_path=_yaml(tmp_path))

    assert ZAMMAD_CONNECTION_CONTRACT_VERSION == 2
    assert settings.zammad_connection.origin == "https://zammad.from-env.example"
    assert settings.zammad_connection.timeout_seconds == 12.5
    assert settings.zammad_connection.allow_private_origin is True


@pytest.mark.parametrize(
    "origin", ("http://zammad.example", "https://zammad.example/api/v1", "https://a..example")
)
def test_connection_rejects_non_origin_urls_without_echoing_input(origin: str) -> None:
    with pytest.raises(ValueError) as raised:
        ZammadConnection(origin=origin, api_token=SecretStr("token"))

    assert origin not in str(raised.value)


def test_conflicting_environment_aliases_do_not_disclose_tokens(
    monkeypatch, tmp_path: Path
) -> None:
    secret = "canonical-token-must-not-appear"
    monkeypatch.setenv("ZAMMAD_API_TOKEN", secret)
    monkeypatch.setenv("ZAMMAD__API_TOKEN", "different-token")

    with pytest.raises(ConfigValidationError) as raised:
        load_settings(config_path=_yaml(tmp_path))

    assert secret not in str(raised.value)
    assert "different-token" not in str(raised.value)


def test_validated_connection_drives_safe_client_transport(tmp_path: Path) -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example/",
                "api_token": "test-token",
                "webhook_hmac_secret": _WEBHOOK_SECRET,
                "timeout_seconds": 7.0,
            },
            "storage": {"root": tmp_path},
            "hardening": {"transport": {"allow_private_networks": True, "trust_env": True}},
        }
    )
    validate_settings(settings)
    client = AsyncZammadClient(connection=settings.zammad_connection)

    assert settings.zammad_connection.api_root == "https://zammad.example/api/v1"
    assert settings.zammad_connection.timeout_seconds == 7.0
    asyncio.run(client.aclose())
