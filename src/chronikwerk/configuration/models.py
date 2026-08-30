"""Compose the validated runtime configuration model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from chronikwerk.configuration.sections import (
    AdminSettings,
    AdmissionSettings,
    FieldsSettings,
    HardeningSettings,
    ObservabilitySettings,
    PdfSettings,
    ServerSettings,
    StorageSettings,
    WorkflowSettings,
)
from chronikwerk.configuration.signing import (
    SigningSettings,
)
from chronikwerk.configuration.zammad import (
    ZAMMAD_CONNECTION_CONTRACT_VERSION as ZAMMAD_CONNECTION_CONTRACT_VERSION,
)
from chronikwerk.configuration.zammad import (
    ZammadConnection,
    ZammadSettings,
)
from chronikwerk.configuration.zammad import (
    canonicalize_zammad_origin as canonicalize_zammad_origin,
)


class Settings(BaseSettings):
    """Aggregate all validated service configuration sections."""

    model_config = SettingsConfigDict(
        env_prefix="",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    server: ServerSettings = Field(default_factory=ServerSettings)
    zammad: ZammadSettings
    workflow: WorkflowSettings = Field(default_factory=WorkflowSettings)
    fields: FieldsSettings = Field(default_factory=FieldsSettings)
    storage: StorageSettings
    pdf: PdfSettings = Field(default_factory=PdfSettings)
    signing: SigningSettings = Field(default_factory=SigningSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    hardening: HardeningSettings = Field(default_factory=HardeningSettings)
    admission: AdmissionSettings = Field(default_factory=AdmissionSettings)
    admin: AdminSettings = Field(default_factory=AdminSettings)
    retry_bearer_token: SecretStr | None = None

    @property
    def zammad_connection(self) -> ZammadConnection:
        """Build the fixed-safe runtime connection from legacy configuration fields."""
        return ZammadConnection(
            origin=str(self.zammad.base_url),
            api_token=self.zammad.api_token,
            timeout_seconds=self.zammad.timeout_seconds,
            allow_private_origin=self.hardening.transport.allow_private_networks,
            trust_environment=self.hardening.transport.trust_env,
            allow_insecure_http=self.hardening.transport.allow_insecure_http,
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Settings:
        """
        Construct Settings from a mapping without reading environment variables.

        Useful in tests where we want to pass nested dicts and keep mypy happy.
        """

        class _InitOnlySettings(Settings):
            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls: type[BaseSettings],
                init_settings: PydanticBaseSettingsSource,
                env_settings: PydanticBaseSettingsSource,
                dotenv_settings: PydanticBaseSettingsSource,
                file_secret_settings: PydanticBaseSettingsSource,
            ) -> tuple[PydanticBaseSettingsSource, ...]:
                """Use only the supplied mapping so ambient settings cannot affect callers."""
                return (init_settings,)

        return _InitOnlySettings(**dict(data))

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource | Any, ...]:
        """Order settings sources so explicit environment values override managed configuration."""
        # Keep this order explicit: process environment, constructor/YAML
        # values, dotenv, file secrets, then Pydantic defaults.
        return (
            env_settings,
            init_settings,
            dotenv_settings,
            file_secret_settings,
        )
