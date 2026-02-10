from __future__ import annotations

from pathlib import Path

import pytest

from pydantic import ValidationError

from zammad_pdf_archiver.config.load import load_settings
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.validate import ConfigValidationError, validate_settings


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = [
        "CONFIG_PATH",
        "SERVER_HOST",
        "SERVER_PORT",
        "WEBHOOK_SHARED_SECRET",
        "WEBHOOK_HMAC_SECRET",
        "HARDENING_WEBHOOK_ALLOW_UNSIGNED",
        "HARDENING_TRANSPORT_ALLOW_LOCAL_UPSTREAMS",
        "ZAMMAD_BASE_URL",
        "ZAMMAD_URL",
        "ZAMMAD_API_TOKEN",
        "ZAMMAD_TIMEOUT_SECONDS",
        "ZAMMAD_VERIFY_TLS",
        "STORAGE_ROOT",
        "SIGNING_ENABLED",
        "SIGNING_PFX_PATH",
        "SIGNING_PFX_PASSWORD",
        "SIGNING_CERT_PATH",
        "SIGNING_KEY_PATH",
        "LOG_LEVEL",
        "LOG_JSON",
        # Nested form (supported by pydantic-settings)
        "ZAMMAD__BASE_URL",
        "ZAMMAD__API_TOKEN",
        "ZAMMAD__WEBHOOK_HMAC_SECRET",
        "STORAGE__ROOT",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def test_missing_required_env_vars_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    with pytest.raises(ConfigValidationError) as exc:
        load_settings()

    msg = str(exc.value)
    assert "zammad.base_url" in msg
    assert "ZAMMAD_BASE_URL" in msg
    assert "zammad.api_token" in msg
    assert "ZAMMAD_API_TOKEN" in msg
    assert "storage.root" in msg
    assert "STORAGE_ROOT" in msg


def test_yaml_loading_works(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "zammad:",
                "  base_url: https://zammad.example.local",
                "  api_token: test-token",
                "storage:",
                "  root: /mnt/archive",
                "hardening:",
                "  webhook:",
                "    allow_unsigned: true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path)
    assert str(settings.zammad.base_url).rstrip("/") == "https://zammad.example.local"
    assert settings.zammad.api_token.get_secret_value() == "test-token"
    assert settings.storage.root.as_posix() == "/mnt/archive"


def test_env_overrides_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "zammad:",
                "  base_url: https://zammad.from-yaml.local",
                "  api_token: yaml-token",
                "storage:",
                "  root: /mnt/archive",
                "hardening:",
                "  webhook:",
                "    allow_unsigned: true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("ZAMMAD_BASE_URL", "https://zammad.from-env.local")
    monkeypatch.setenv("ZAMMAD_API_TOKEN", "env-token")

    settings = load_settings(config_path=config_path)
    assert str(settings.zammad.base_url).rstrip("/") == "https://zammad.from-env.local"
    assert settings.zammad.api_token.get_secret_value() == "env-token"


def test_explicit_config_path_missing_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    missing = tmp_path / "missing.yaml"
    with pytest.raises(ConfigValidationError) as exc:
        load_settings(config_path=missing)

    assert "CONFIG_PATH" in str(exc.value)
    assert "Config file not found" in str(exc.value)


def test_yaml_root_must_be_mapping(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    config_path = tmp_path / "config.yaml"
    config_path.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(ConfigValidationError) as exc:
        load_settings(config_path=config_path)

    assert "YAML root must be a mapping/object" in str(exc.value)


def test_workflow_redis_backend_requires_redis_url() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings.from_mapping(
            {
                "zammad": {"base_url": "https://z.example", "api_token": "t"},
                "storage": {"root": "/mnt"},
                "hardening": {"webhook": {"allow_unsigned": True}},
                "workflow": {"idempotency_backend": "redis"},
            }
        )
    assert "redis_url" in str(exc_info.value).lower() or "redis" in str(exc_info.value).lower()


def test_pdf_attachment_binary_settings_loaded() -> None:
    """Pdf attachment binary inclusion settings (PRD §8.2) are accepted and have defaults."""
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://z.example", "api_token": "t"},
            "storage": {"root": "/mnt"},
            "hardening": {"webhook": {"allow_unsigned": True}},
        }
    )
    assert settings.pdf.include_attachment_binary is False
    assert settings.pdf.max_attachment_bytes_per_file == 10 * 1024 * 1024
    assert settings.pdf.max_total_attachment_bytes == 50 * 1024 * 1024

    settings2 = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://z.example", "api_token": "t"},
            "storage": {"root": "/mnt"},
            "hardening": {"webhook": {"allow_unsigned": True}},
            "pdf": {
                "include_attachment_binary": True,
                "max_attachment_bytes_per_file": 1024,
                "max_total_attachment_bytes": 4096,
            },
        }
    )
    assert settings2.pdf.include_attachment_binary is True
    assert settings2.pdf.max_attachment_bytes_per_file == 1024
    assert settings2.pdf.max_total_attachment_bytes == 4096


def test_validate_settings_rejects_invalid_log_level() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": "/mnt/archive"},
            "observability": {"log_level": "VERBOSE"},
            "hardening": {"webhook": {"allow_unsigned": True}},
        }
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    msg = str(exc.value)
    assert "observability.log_level" in msg
    assert "Unsupported log level" in msg


def test_validate_settings_requires_webhook_secret_by_default() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": "/mnt/archive"},
        }
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    assert "zammad.webhook_hmac_secret" in str(exc.value)


def test_validate_settings_allows_unsigned_webhooks_when_enabled() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": "/mnt/archive"},
            "hardening": {"webhook": {"allow_unsigned": True}},
        }
    )

    # Should not raise.
    validate_settings(settings)


def test_validate_settings_rejects_plain_http_upstream_by_default() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "http://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": "/mnt/archive"},
            "hardening": {"webhook": {"allow_unsigned": True}},
        }
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    assert "zammad.base_url" in str(exc.value)


def test_validate_settings_rejects_insecure_tls_by_default() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": "test-token",
                "verify_tls": False,
            },
            "storage": {"root": "/mnt/archive"},
            "hardening": {"webhook": {"allow_unsigned": True}},
        }
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    assert "zammad.verify_tls" in str(exc.value)


def test_validate_settings_rejects_loopback_upstream_by_default() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://127.0.0.1", "api_token": "test-token"},
            "storage": {"root": "/mnt/archive"},
            "hardening": {"webhook": {"allow_unsigned": True}},
        }
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    msg = str(exc.value)
    assert "zammad.base_url" in msg
    assert "allow_local_upstreams" in msg


def test_validate_settings_allows_loopback_upstream_when_explicitly_enabled() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://127.0.0.1", "api_token": "test-token"},
            "storage": {"root": "/mnt/archive"},
            "hardening": {
                "webhook": {"allow_unsigned": True},
                "transport": {"allow_local_upstreams": True},
            },
        }
    )

    validate_settings(settings)


def test_load_settings_rejects_signing_enabled_without_pfx_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "zammad:",
                "  base_url: https://zammad.example.local",
                "  api_token: test-token",
                "storage:",
                "  root: /mnt/archive",
                "hardening:",
                "  webhook:",
                "    allow_unsigned: true",
                "signing:",
                "  enabled: true",
                "  pades:",
                "    cert_path: /run/secrets/signer.crt",
                "    key_path: /run/secrets/signer.key",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigValidationError) as exc:
        load_settings(config_path=config_path)

    assert "signing.pfx_path is missing" in str(exc.value)


def test_load_settings_accepts_signing_enabled_with_pfx_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "zammad:",
                "  base_url: https://zammad.example.local",
                "  api_token: test-token",
                "storage:",
                "  root: /mnt/archive",
                "hardening:",
                "  webhook:",
                "    allow_unsigned: true",
                "signing:",
                "  enabled: true",
                "  pfx_path: /run/secrets/signing.pfx",
                "",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path)
    assert settings.signing.enabled is True
    assert str(settings.signing.pfx_path) == "/run/secrets/signing.pfx"
