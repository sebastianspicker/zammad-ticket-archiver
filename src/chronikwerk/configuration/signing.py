"""Define PDF signing and timestamp configuration sections."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic.networks import AnyHttpUrl

from chronikwerk.configuration.sections import _BaseSection


class SigningPadesSettings(_BaseSection):
    """Configure visible metadata embedded in PAdES signatures."""

    reason: str = "Ticket Archivierung"
    location: str = "Datacenter"


class SigningTimestampRfc3161Settings(_BaseSection):
    """Configure the optional RFC 3161 timestamp authority."""

    tsa_url: AnyHttpUrl | None = None
    timeout_seconds: float = Field(default=10.0, gt=0)
    ca_bundle_path: Path | None = None
    user: str | None = None
    password: SecretStr | None = None


class SigningTimestampSettings(_BaseSection):
    """Configure optional timestamping for signed documents."""

    enabled: bool = False
    rfc3161: SigningTimestampRfc3161Settings = Field(
        default_factory=SigningTimestampRfc3161Settings
    )


class SigningSettings(_BaseSection):
    """Configure optional PAdES signing and its required credentials."""

    enabled: bool = False
    # PKCS#12/PFX bundle with signer cert + private key.
    pfx_path: Path | None = None
    pfx_password: SecretStr | None = None
    pades: SigningPadesSettings = Field(default_factory=SigningPadesSettings)
    timestamp: SigningTimestampSettings = Field(default_factory=SigningTimestampSettings)

    @model_validator(mode="after")
    def _require_material_if_enabled(self) -> SigningSettings:
        if self.enabled and self.pfx_path is None:
            raise ValueError(
                "Signing is enabled but signing.pfx_path is missing. "
                "The current implementation requires a PKCS#12/PFX bundle."
            )

        if self.timestamp.enabled and not self.enabled:
            raise ValueError("Timestamping requires signing.enabled")

        if self.timestamp.enabled and self.timestamp.rfc3161.tsa_url is None:
            raise ValueError(
                "Timestamping is enabled but signing.timestamp.rfc3161.tsa_url is missing"
            )

        return self
