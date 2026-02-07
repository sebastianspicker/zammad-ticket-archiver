from __future__ import annotations

from pathlib import Path

import pytest

from zammad_pdf_archiver.config.load import load_settings


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = [
        "CONFIG_PATH",
        # Required keys
        "ZAMMAD_BASE_URL",
        "ZAMMAD_API_TOKEN",
        "STORAGE_ROOT",
        # Webhook auth default (fail closed unless secret configured)
        "WEBHOOK_HMAC_SECRET",
        "HARDENING_WEBHOOK_ALLOW_UNSIGNED",
        # Current env keys
        "PDF_TEMPLATE_VARIANT",
        "PDF_LOCALE",
        "PDF_TIMEZONE",
        # Legacy keys still present in .env.example
        "TEMPLATE_VARIANT",
        "RENDER_LOCALE",
        "RENDER_TIMEZONE",
        "SIGNING_REASON",
        "SIGNING_LOCATION",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def test_env_aliases_from_env_example_are_honored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_env(monkeypatch)

    monkeypatch.setenv("ZAMMAD_BASE_URL", "https://zammad.example.local")
    monkeypatch.setenv("ZAMMAD_API_TOKEN", "test-token")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("HARDENING_WEBHOOK_ALLOW_UNSIGNED", "true")

    monkeypatch.setenv("TEMPLATE_VARIANT", "minimal")
    monkeypatch.setenv("RENDER_LOCALE", "en_US")
    monkeypatch.setenv("RENDER_TIMEZONE", "UTC")
    monkeypatch.setenv("SIGNING_REASON", "Unit Test Reason")
    monkeypatch.setenv("SIGNING_LOCATION", "Unit Test Location")

    settings = load_settings()
    assert settings.pdf.template_variant == "minimal"
    assert settings.pdf.locale == "en_US"
    assert settings.pdf.timezone == "UTC"
    assert settings.signing.pades.reason == "Unit Test Reason"
    assert settings.signing.pades.location == "Unit Test Location"
