"""Cover the preservation settings: defaults, env keys, validation, redaction and warnings."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from chronikwerk.cli import cmd_dump_config
from chronikwerk.configuration.load import load_settings
from chronikwerk.configuration.models import Settings
from chronikwerk.configuration.revisions import MANAGED_FIELDS, secret_presence
from chronikwerk.configuration.signing import SigningSettings
from chronikwerk.configuration.validation import preservation_requirements
from tests.support.settings_factory import make_settings, write_test_config

_S3 = {
    "backend": "s3",
    "s3": {"bucket": "archive", "access_key_id": "AKIAEXAMPLE", "secret_access_key": "s3-secret"},
}


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Build valid settings with section overrides."""
    return make_settings(str(tmp_path), overrides=overrides)


def _isolate_environment(monkeypatch: pytest.MonkeyPatch, cwd: Path) -> None:
    """Drop ambient Zammad/storage variables so the test config file is the only source."""
    for name in ("CONFIG_PATH", "ZAMMAD__BASE_URL", "ZAMMAD__API_TOKEN", "STORAGE__ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ZAMMAD_ALLOW_PRIVATE_ORIGIN", "true")
    monkeypatch.chdir(cwd)


def _issue_text(error: ValidationError) -> str:
    """Join the messages of all validation errors."""
    return " ".join(str(item["msg"]) for item in error.errors())


def test_defaults_match_the_published_contract(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    assert settings.journal.path == Path("/var/lib/chronikwerk/journal.sqlite3")
    assert settings.capture.consistency_attempts == 3
    assert settings.capture.max_attachment_bytes == 104_857_600
    assert settings.capture.max_package_bytes == 1_073_741_824
    assert settings.capture.download_timeout_seconds == 300
    assert settings.capture.temp_dir == Path("/var/lib/chronikwerk/tmp")
    assert settings.capture.max_temp_bytes == 4_294_967_296
    assert settings.capture.retry.max_attempts == 5
    assert settings.capture.retry.max_delay_seconds == 3600
    assert settings.admission.max_pending == 1000
    assert settings.fields.record_class == "archive_record_class"
    assert settings.pdf.reading_copies == ["pdf/a-2u", "pdf/ua-1"]
    assert settings.signing.pades.level == "B-LT"
    assert settings.seal.pfx_path is None and settings.seal.pfx_password is None
    assert settings.verify.trust_anchors_path is None
    assert settings.hardening.transport.validation_hosts == []
    assert settings.storage.backend == "filesystem"
    assert settings.storage.filesystem.worm_enforced is False
    assert settings.storage.s3.endpoint_url is None
    assert settings.storage.s3.region is None
    assert settings.storage.s3.bucket is None
    assert settings.storage.s3.object_lock_mode == "COMPLIANCE"
    assert settings.storage.s3.staging_retention_days == 7
    assert settings.retention.policy_path == Path("/etc/chronikwerk/retention.yaml")
    assert settings.reconcile.interval_seconds == 86_400
    assert settings.reconcile.stale_after_seconds == 86_400
    assert settings.fixity.cycle_days == 90
    assert settings.alerts.webhook_url is None
    assert settings.renewal.lead_days == 180


def test_nested_environment_keys_map_to_the_new_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STORAGE__S3__BUCKET", "archive-bucket")
    monkeypatch.setenv("CAPTURE__RETRY__MAX_ATTEMPTS", "9")
    monkeypatch.setenv("PDF__READING_COPIES", '["pdf/ua-1"]')
    monkeypatch.setenv("SEAL__PFX_PASSWORD", "seal-password")

    settings = Settings(
        zammad={"base_url": "https://zammad.example.local", "api_token": "t"},  # type: ignore[arg-type]
        storage={"root": tmp_path},  # type: ignore[arg-type]
    )

    assert settings.storage.s3.bucket == "archive-bucket"
    assert settings.capture.retry.max_attempts == 9
    assert settings.pdf.reading_copies == ["pdf/ua-1"]
    assert settings.seal.pfx_password is not None
    assert settings.seal.pfx_password.get_secret_value() == "seal-password"


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"storage": {"backend": "s3"}}, "storage.s3.bucket"),
        (
            {"storage": {"backend": "s3", "s3": {"bucket": "b"}}},
            "storage.s3.access_key_id, storage.s3.secret_access_key",
        ),
        ({"capture": {"max_package_bytes": 10, "max_attachment_bytes": 11}}, "max_package_bytes"),
        (
            {"capture": {"max_temp_bytes": 10, "max_package_bytes": 11, "max_attachment_bytes": 1}},
            "max_temp_bytes",
        ),
        ({"capture": {"consistency_attempts": 0}}, "greater than or equal to 1"),
        ({"pdf": {"reading_copies": ["pdf/x-9"]}}, "pdf/a-2u"),
        ({"pdf": {"reading_copies": []}}, "must not be empty"),
        ({"pdf": {"reading_copies": ["pdf/ua-1", "pdf/ua-1"]}}, "duplicates"),
        ({"signing": {"pades": {"level": "B-B"}}}, "B-T"),
        ({"storage": {"s3": {"staging_retention_days": 366}}}, "less than or equal to 365"),
        ({"storage": {"s3": {"endpoint_url": "http://minio.local"}}}, "https"),
        ({"alerts": {"webhook_url": "http://alerts.example"}}, "https"),
        ({"reconcile": {"interval_seconds": 59}}, "greater than or equal to 60"),
        ({"fixity": {"cycle_days": 3651}}, "less than or equal to 3650"),
        ({"renewal": {"lead_days": 0}}, "greater than or equal to 1"),
        ({"admission": {"max_pending": 0}}, "greater than or equal to 1"),
    ],
)
def test_invalid_values_are_rejected(
    tmp_path: Path, overrides: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        _settings(tmp_path, **overrides)

    assert fragment in _issue_text(caught.value)


@pytest.mark.parametrize(
    "host", ["https://ocsp.example", "ocsp.example:80", "ocsp.example/path", "*.example", ""]
)
def test_validation_hosts_must_be_bare_host_names(tmp_path: Path, host: str) -> None:
    with pytest.raises(ValidationError) as caught:
        _settings(tmp_path, hardening={"transport": {"validation_hosts": [host]}})

    assert "bare host name" in _issue_text(caught.value)


def test_validation_hosts_accept_and_lowercase_host_names(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        hardening={"transport": {"validation_hosts": ["OCSP.Example.com", "crl-1.example.com"]}},
    )

    hosts = settings.hardening.transport.validation_hosts
    assert hosts == ["ocsp.example.com", "crl-1.example.com"]


def test_s3_backend_with_bucket_and_credentials_is_valid(tmp_path: Path) -> None:
    settings = _settings(tmp_path, storage=_S3)

    assert settings.storage.backend == "s3"
    assert settings.storage.s3.bucket == "archive"


def test_secrets_are_redacted_in_dump_config_and_not_managed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.yaml"
    write_test_config(config, tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8")
        + "seal:\n  pfx_path: /etc/seal.pfx\n  pfx_password: seal-pw-secret\n",
        encoding="utf-8",
    )
    _isolate_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("CONFIG_PATH", str(config))
    monkeypatch.setenv("STORAGE__BACKEND", "s3")
    monkeypatch.setenv("STORAGE__S3__BUCKET", "archive")
    monkeypatch.setenv("STORAGE__S3__ACCESS_KEY_ID", "AKIA-key-id-secret")
    monkeypatch.setenv("STORAGE__S3__SECRET_ACCESS_KEY", "s3-secret-value")

    assert cmd_dump_config(argparse.Namespace(config=None)) == 0
    output = capsys.readouterr().out

    for secret in ("seal-pw-secret", "AKIA-key-id-secret", "s3-secret-value"):
        assert secret not in output
    dumped = json.loads(output)
    assert dumped["seal"]["pfx_password"] == "[redacted]"
    assert dumped["storage"]["s3"]["secret_access_key"] == "[redacted]"
    managed = {field.path for field in MANAGED_FIELDS}
    assert not managed & {
        "seal.pfx_password",
        "storage.s3.access_key_id",
        "storage.s3.secret_access_key",
    }
    presence = secret_presence(load_settings())
    assert presence["seal.pfx_password"] is True
    assert presence["storage.s3.access_key_id"] is True


def test_explicit_filename_pattern_logs_a_deprecation_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    config = tmp_path / "config.yaml"
    write_test_config(config, tmp_path)
    _isolate_environment(monkeypatch, tmp_path)

    with caplog.at_level(logging.WARNING, logger="chronikwerk.configuration.load"):
        load_settings(config_path=config)
    assert "config.deprecated_setting" not in caplog.text

    config.write_text(
        config.read_text(encoding="utf-8").replace(
            f"  root: {tmp_path}",
            f"  root: {tmp_path}\n  filename_pattern: 'Ticket-{{ticket_number}}.pdf'",
        ),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="chronikwerk.configuration.load"):
        load_settings(config_path=config)

    assert "config.deprecated_setting key=storage.filename_pattern" in caplog.text


def test_preservation_requirements_lists_every_missing_piece(tmp_path: Path) -> None:
    assert preservation_requirements(_settings(tmp_path)) == [
        "seal.pfx_path is missing",
        "verify.trust_anchors_path is missing",
        "signing.timestamp.enabled must be true",
        "signing.timestamp.rfc3161.tsa_url is missing",
    ]


def test_preservation_requirements_is_empty_when_complete(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        seal={"pfx_path": "/etc/seal.pfx"},
        verify={"trust_anchors_path": "/etc/anchors.pem"},
        signing={
            "enabled": True,
            "pfx_path": "/etc/seal.pfx",
            "timestamp": {
                "enabled": True,
                "rfc3161": {"tsa_url": "https://tsa.example/tsr"},
            },
        },
    )

    assert preservation_requirements(settings) == []


def test_timestamping_does_not_require_pdf_signing() -> None:
    """The manifest seal needs a timestamp; the PAdES signature on the PDF stays optional."""
    settings = SigningSettings.model_validate(
        {
            "enabled": False,
            "timestamp": {"enabled": True, "rfc3161": {"tsa_url": "https://tsa.example.org"}},
        }
    )

    assert settings.enabled is False
    assert settings.timestamp.enabled is True
