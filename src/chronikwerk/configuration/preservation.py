"""Define configuration sections for the preservation design (journal, capture, store, seal)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic.networks import AnyHttpUrl

from chronikwerk.configuration.sections import _BaseSection, require_https_url


class JournalSettings(_BaseSection):
    """Configure the request journal and catalog database."""

    path: Path = Path("/var/lib/chronikwerk/journal.sqlite3")

    @field_validator("path")
    @classmethod
    def _expand_path(cls, value: Path) -> Path:
        return value.expanduser()


class CaptureRetrySettings(_BaseSection):
    """Configure automatic retries of transient capture failures."""

    max_attempts: int = Field(default=5, ge=1)
    max_delay_seconds: float = Field(default=3600.0, gt=0)


class CaptureSettings(_BaseSection):
    """Configure source collection limits and the local build area."""

    consistency_attempts: int = Field(default=3, ge=1)
    max_attachment_bytes: int = Field(default=104_857_600, gt=0)
    max_package_bytes: int = Field(default=1_073_741_824, gt=0)
    download_timeout_seconds: float = Field(default=300.0, gt=0)
    temp_dir: Path = Path("/var/lib/chronikwerk/tmp")
    max_temp_bytes: int = Field(default=4_294_967_296, gt=0)
    retry: CaptureRetrySettings = Field(default_factory=CaptureRetrySettings)

    @field_validator("temp_dir")
    @classmethod
    def _expand_temp_dir(cls, value: Path) -> Path:
        return value.expanduser()

    @model_validator(mode="after")
    def _package_holds_an_attachment(self) -> CaptureSettings:
        if self.max_package_bytes < self.max_attachment_bytes:
            raise ValueError(
                "capture.max_package_bytes must not be smaller than capture.max_attachment_bytes"
            )
        if self.max_temp_bytes < self.max_package_bytes:
            raise ValueError(
                "capture.max_temp_bytes must not be smaller than capture.max_package_bytes"
            )
        return self


class SealSettings(_BaseSection):
    """Configure the sealing identity (may reuse the PDF signing PFX)."""

    pfx_path: Path | None = None
    pfx_password: SecretStr | None = None


class VerifySettings(_BaseSection):
    """Configure offline verification material."""

    trust_anchors_path: Path | None = None


class RetentionSettings(_BaseSection):
    """Configure where the record-class retention policy is read from."""

    policy_path: Path = Path("/etc/chronikwerk/retention.yaml")


class ReconcileSettings(_BaseSection):
    """Configure reconciliation of tagged tickets against the journal."""

    interval_seconds: int = Field(default=86_400, ge=60)
    stale_after_seconds: int = Field(default=86_400, ge=60)


class FixitySettings(_BaseSection):
    """Configure rolling fixity checks."""

    cycle_days: int = Field(default=90, ge=1, le=3650)


class AlertsSettings(_BaseSection):
    """Configure the out-of-band alert endpoint."""

    webhook_url: AnyHttpUrl | None = None

    @field_validator("webhook_url")
    @classmethod
    def _require_https_webhook(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        return require_https_url(value, key="alerts.webhook_url")


class RenewalSettings(_BaseSection):
    """Configure evidence renewal timing."""

    lead_days: int = Field(default=180, ge=1, le=3650)
