"""NFR5: Disallow plaintext HTTP, disabled TLS, loopback by default; require explicit overrides."""
from __future__ import annotations

import pytest

from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.validate import ConfigValidationError, validate_settings


def test_nfr5_plain_http_rejected_by_default() -> None:
    """NFR5: validate_settings must reject http:// base_url without allow_insecure_http."""
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "http://zammad.local",
                "api_token": "tok",
            },
            "storage": {"root": "/tmp/archive"},
            "hardening": {"transport": {"allow_insecure_http": False}},
        }
    )
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_settings(settings)
    assert any(
        "http" in str(i.message).lower() or "insecure" in str(i.message).lower()
        for i in exc_info.value.issues
    )
