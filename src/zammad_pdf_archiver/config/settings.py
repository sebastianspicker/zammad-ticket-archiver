from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic.networks import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class _BaseSection(BaseModel):
    model_config = {"extra": "forbid"}


class ServerSettings(_BaseSection):
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    webhook_shared_secret: SecretStr | None = None


class ZammadSettings(_BaseSection):
    base_url: AnyHttpUrl
    api_token: SecretStr
    webhook_hmac_secret: SecretStr | None = None
    timeout_seconds: float = Field(default=10.0, gt=0)
    verify_tls: bool = True


class WorkflowSettings(_BaseSection):
    trigger_tag: str = "pdf:sign"
    require_tag: bool = True
    acknowledge_on_success: bool = True
    delivery_id_ttl_seconds: int = Field(default=3600, ge=0)
    # Durable idempotency (PRD §8.2): "memory" (default) or "redis"
    idempotency_backend: str = "memory"
    redis_url: str | None = None

    @model_validator(mode="after")
    def _redis_required_when_backend_redis(self) -> "WorkflowSettings":
        if self.idempotency_backend == "redis" and not (self.redis_url and self.redis_url.strip()):
            raise ValueError(
                "workflow.idempotency_backend is 'redis' but workflow.redis_url is not set"
            )
        return self


class FieldsSettings(_BaseSection):
    archive_path: str = "archive_path"
    archive_user_mode: str = "archive_user_mode"


class StoragePathPolicySanitizeSettings(_BaseSection):
    replace_whitespace: str = "_"
    strip_control_chars: bool = True


class StoragePathPolicySettings(_BaseSection):
    allow_prefixes: list[str] = Field(default_factory=list)
    sanitize: StoragePathPolicySanitizeSettings = Field(
        default_factory=StoragePathPolicySanitizeSettings
    )
    filename_pattern: str = "Ticket-{ticket_number}_{timestamp_utc}.pdf"


class StorageSettings(_BaseSection):
    root: Path
    atomic_write: bool = True
    fsync: bool = True
    path_policy: StoragePathPolicySettings = Field(default_factory=StoragePathPolicySettings)

    @field_validator("root")
    @classmethod
    def _expand_root(cls, value: Path) -> Path:
        return value.expanduser()


class PdfSettings(_BaseSection):
    template_variant: str = "default"  # default|minimal
    locale: str = "de_DE"
    timezone: str = "Europe/Berlin"
    max_articles: int = Field(default=250, ge=0)
    # Optional attachment binary inclusion (PRD §8.2)
    include_attachment_binary: bool = False
    max_attachment_bytes_per_file: int = Field(default=10 * 1024 * 1024, ge=0)  # 10 MiB
    max_total_attachment_bytes: int = Field(default=50 * 1024 * 1024, ge=0)  # 50 MiB

    @property
    def template(self) -> str:
        return self.template_variant


class SigningPadesSettings(_BaseSection):
    cert_path: Path | None = None
    key_path: Path | None = None
    key_password: SecretStr | None = None
    reason: str = "Ticket Archivierung"
    location: str = "Datacenter"


class SigningTimestampRfc3161Settings(_BaseSection):
    tsa_url: AnyHttpUrl | None = None
    timeout_seconds: float = Field(default=10.0, gt=0)
    ca_bundle_path: Path | None = None


class SigningTimestampSettings(_BaseSection):
    enabled: bool = False
    rfc3161: SigningTimestampRfc3161Settings = Field(
        default_factory=SigningTimestampRfc3161Settings
    )


class SigningSettings(_BaseSection):
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

        if self.timestamp.enabled and self.timestamp.rfc3161.tsa_url is None:
            raise ValueError(
                "Timestamping is enabled but signing.timestamp.rfc3161.tsa_url is missing"
            )

        return self


class ObservabilitySettings(_BaseSection):
    log_level: str = "INFO"
    log_format: str | None = None  # json|human (overrides LOG_FORMAT/env when set)
    json_logs: bool = False
    metrics_enabled: bool = False
    # When set, GET /metrics requires Authorization: Bearer <this token> (constant-time compare).
    metrics_bearer_token: SecretStr | None = None
    # When true, GET /healthz omits version and service name (reduces fingerprinting).
    healthz_omit_version: bool = False

    @field_validator("log_format")
    @classmethod
    def _validate_log_format(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized in {"json", "human"}:
            return normalized
        raise ValueError("observability.log_format must be 'json' or 'human'")


class RateLimitSettings(_BaseSection):
    enabled: bool = True
    rps: float = Field(default=5.0, ge=0)
    burst: int = Field(default=10, ge=1)
    include_metrics: bool = False
    # When set (e.g. "X-Forwarded-For"), rate limit key is taken from this header (first value).
    # Trust proxy to set it; use with care.
    client_key_header: str | None = None


class BodySizeLimitSettings(_BaseSection):
    # 0 disables the limit.
    max_bytes: int = Field(default=1024 * 1024, ge=0)


class WebhookHardeningSettings(_BaseSection):
    # If false, /ingest is rejected unless a webhook HMAC secret is configured.
    allow_unsigned: bool = False
    # When enabled, /ingest requires X-Zammad-Delivery and the replay TTL must be > 0.
    require_delivery_id: bool = False


class TransportHardeningSettings(_BaseSection):
    # If true, allow httpx to read HTTP_PROXY/HTTPS_PROXY/NO_PROXY and other env settings.
    trust_env: bool = False
    # Allow plaintext HTTP for upstream URLs (Zammad / TSA). Strongly discouraged.
    allow_insecure_http: bool = False
    # Allow disabling TLS verification for upstream requests. Strongly discouraged.
    allow_insecure_tls: bool = False
    # Allow outbound upstreams that target loopback / link-local addresses.
    allow_local_upstreams: bool = False


class HardeningSettings(_BaseSection):
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    body_size_limit: BodySizeLimitSettings = Field(default_factory=BodySizeLimitSettings)
    webhook: WebhookHardeningSettings = Field(default_factory=WebhookHardeningSettings)
    transport: TransportHardeningSettings = Field(default_factory=TransportHardeningSettings)


class Settings(BaseSettings):
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

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Settings:
        """
        Construct Settings from a mapping without reading environment variables.

        Useful in tests where we want to pass nested dicts and keep mypy happy.
        """
        return cls.model_validate(dict(data))

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (
            env_settings,
            _flat_env_settings_source,
            init_settings,
            dotenv_settings,
            file_secret_settings,
        )


def _flat_env_settings_source() -> dict[str, Any]:
    env = os.environ
    data: dict[str, Any] = {}

    # Server
    if (value := env.get("SERVER_HOST")):
        data.setdefault("server", {})["host"] = value
    if (value := env.get("SERVER_PORT")):
        data.setdefault("server", {})["port"] = value
    if (value := env.get("WEBHOOK_SHARED_SECRET")):
        data.setdefault("server", {})["webhook_shared_secret"] = value

    # Zammad
    if (value := env.get("ZAMMAD_BASE_URL")):
        data.setdefault("zammad", {})["base_url"] = value
    elif (value := env.get("ZAMMAD_URL")):
        data.setdefault("zammad", {})["base_url"] = value
    if (value := env.get("ZAMMAD_API_TOKEN")):
        data.setdefault("zammad", {})["api_token"] = value
    if (value := env.get("WEBHOOK_HMAC_SECRET")):
        data.setdefault("zammad", {})["webhook_hmac_secret"] = value
    if (value := env.get("ZAMMAD_TIMEOUT_SECONDS")):
        data.setdefault("zammad", {})["timeout_seconds"] = value
    if (value := env.get("ZAMMAD_VERIFY_TLS")):
        data.setdefault("zammad", {})["verify_tls"] = value

    # Workflow / Fields
    if (value := env.get("WORKFLOW_TRIGGER_TAG")):
        data.setdefault("workflow", {})["trigger_tag"] = value
    if (value := env.get("WORKFLOW_REQUIRE_TAG")):
        data.setdefault("workflow", {})["require_tag"] = value
    if (value := env.get("WORKFLOW_DELIVERY_ID_TTL_SECONDS")):
        data.setdefault("workflow", {})["delivery_id_ttl_seconds"] = value
    if (value := env.get("IDEMPOTENCY_BACKEND")):
        data.setdefault("workflow", {})["idempotency_backend"] = value
    if (value := env.get("REDIS_URL")):
        data.setdefault("workflow", {})["redis_url"] = value
    if (value := env.get("FIELDS_ARCHIVE_PATH")):
        data.setdefault("fields", {})["archive_path"] = value
    if (value := env.get("FIELDS_ARCHIVE_USER_MODE")):
        data.setdefault("fields", {})["archive_user_mode"] = value

    # Storage
    if (value := env.get("STORAGE_ROOT")):
        data.setdefault("storage", {})["root"] = value
    if (value := env.get("STORAGE_ATOMIC_WRITE")):
        data.setdefault("storage", {})["atomic_write"] = value
    if (value := env.get("STORAGE_FSYNC")):
        data.setdefault("storage", {})["fsync"] = value

    # PDF
    if (value := env.get("PDF_TEMPLATE_VARIANT")):
        data.setdefault("pdf", {})["template_variant"] = value
    elif (value := env.get("TEMPLATE_VARIANT")):
        # Backwards-compatible alias (present in older deployment env templates).
        data.setdefault("pdf", {})["template_variant"] = value
    if (value := env.get("PDF_LOCALE")):
        data.setdefault("pdf", {})["locale"] = value
    elif (value := env.get("RENDER_LOCALE")):
        data.setdefault("pdf", {})["locale"] = value
    if (value := env.get("PDF_TIMEZONE")):
        data.setdefault("pdf", {})["timezone"] = value
    elif (value := env.get("RENDER_TIMEZONE")):
        data.setdefault("pdf", {})["timezone"] = value
    if (value := env.get("PDF_MAX_ARTICLES")):
        data.setdefault("pdf", {})["max_articles"] = value
    if (value := env.get("PDF_INCLUDE_ATTACHMENT_BINARY")):
        data.setdefault("pdf", {})["include_attachment_binary"] = value
    if (value := env.get("PDF_MAX_ATTACHMENT_BYTES_PER_FILE")):
        data.setdefault("pdf", {})["max_attachment_bytes_per_file"] = value
    if (value := env.get("PDF_MAX_TOTAL_ATTACHMENT_BYTES")):
        data.setdefault("pdf", {})["max_total_attachment_bytes"] = value

    # Signing
    if (value := env.get("SIGNING_ENABLED")):
        data.setdefault("signing", {})["enabled"] = value
    if (value := env.get("SIGNING_PFX_PATH")):
        data.setdefault("signing", {})["pfx_path"] = value
    if (value := env.get("SIGNING_PFX_PASSWORD")):
        data.setdefault("signing", {})["pfx_password"] = value
    if (value := env.get("SIGNING_CERT_PATH")):
        data.setdefault("signing", {}).setdefault("pades", {})["cert_path"] = value
    if (value := env.get("SIGNING_KEY_PATH")):
        data.setdefault("signing", {}).setdefault("pades", {})["key_path"] = value
    if (value := env.get("SIGNING_KEY_PASSWORD")):
        data.setdefault("signing", {}).setdefault("pades", {})["key_password"] = value
    if (value := env.get("SIGNING_REASON")):
        data.setdefault("signing", {}).setdefault("pades", {})["reason"] = value
    if (value := env.get("SIGNING_LOCATION")):
        data.setdefault("signing", {}).setdefault("pades", {})["location"] = value
    if (value := env.get("TSA_ENABLED")):
        data.setdefault("signing", {}).setdefault("timestamp", {})["enabled"] = value
    if (value := env.get("TSA_URL")):
        data.setdefault("signing", {}).setdefault("timestamp", {}).setdefault("rfc3161", {})[
            "tsa_url"
        ] = value
    if (value := env.get("TSA_TIMEOUT_SECONDS")):
        data.setdefault("signing", {}).setdefault("timestamp", {}).setdefault("rfc3161", {})[
            "timeout_seconds"
        ] = value
    if (value := env.get("TSA_CA_BUNDLE_PATH")):
        data.setdefault("signing", {}).setdefault("timestamp", {}).setdefault("rfc3161", {})[
            "ca_bundle_path"
        ] = value

    # Observability
    if (value := env.get("LOG_LEVEL")):
        data.setdefault("observability", {})["log_level"] = value
    if (value := env.get("LOG_FORMAT")):
        data.setdefault("observability", {})["log_format"] = value
    if (value := env.get("LOG_JSON")):
        data.setdefault("observability", {})["json_logs"] = value
    if (value := env.get("METRICS_ENABLED")):
        data.setdefault("observability", {})["metrics_enabled"] = value
    if (value := env.get("OBSERVABILITY_METRICS_ENABLED")):
        data.setdefault("observability", {})["metrics_enabled"] = value
    if (value := env.get("METRICS_BEARER_TOKEN")):
        data.setdefault("observability", {})["metrics_bearer_token"] = value
    if (value := env.get("HEALTHZ_OMIT_VERSION")):
        data.setdefault("observability", {})["healthz_omit_version"] = value

    # Hardening
    if (value := env.get("RATE_LIMIT_ENABLED")):
        data.setdefault("hardening", {}).setdefault("rate_limit", {})["enabled"] = value
    if (value := env.get("RATE_LIMIT_RPS")):
        data.setdefault("hardening", {}).setdefault("rate_limit", {})["rps"] = value
    if (value := env.get("RATE_LIMIT_BURST")):
        data.setdefault("hardening", {}).setdefault("rate_limit", {})["burst"] = value
    if (value := env.get("RATE_LIMIT_INCLUDE_METRICS")):
        data.setdefault("hardening", {}).setdefault("rate_limit", {})["include_metrics"] = value
    if (value := env.get("RATE_LIMIT_CLIENT_KEY_HEADER")):
        data.setdefault("hardening", {}).setdefault("rate_limit", {})["client_key_header"] = value
    if (value := env.get("MAX_BODY_BYTES")):
        data.setdefault("hardening", {}).setdefault("body_size_limit", {})["max_bytes"] = value
    if (value := env.get("HARDENING_WEBHOOK_ALLOW_UNSIGNED")):
        data.setdefault("hardening", {}).setdefault("webhook", {})["allow_unsigned"] = value
    if (value := env.get("HARDENING_WEBHOOK_REQUIRE_DELIVERY_ID")):
        data.setdefault("hardening", {}).setdefault("webhook", {})["require_delivery_id"] = value
    if (value := env.get("HARDENING_TRANSPORT_TRUST_ENV")):
        data.setdefault("hardening", {}).setdefault("transport", {})["trust_env"] = value
    if (value := env.get("HARDENING_TRANSPORT_ALLOW_INSECURE_HTTP")):
        data.setdefault("hardening", {}).setdefault("transport", {})["allow_insecure_http"] = value
    if (value := env.get("HARDENING_TRANSPORT_ALLOW_INSECURE_TLS")):
        data.setdefault("hardening", {}).setdefault("transport", {})["allow_insecure_tls"] = value
    if (value := env.get("HARDENING_TRANSPORT_ALLOW_LOCAL_UPSTREAMS")):
        data.setdefault("hardening", {}).setdefault("transport", {})["allow_local_upstreams"] = (
            value
        )

    return data
