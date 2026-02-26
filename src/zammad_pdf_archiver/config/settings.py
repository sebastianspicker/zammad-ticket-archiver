from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic.networks import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

from zammad_pdf_archiver.config.env_aliases import get_flat_env_settings_source


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
    execution_backend: str = "inprocess"  # inprocess|redis_queue
    # Durable idempotency (PRD §8.2): "memory" (default) or "redis"
    idempotency_backend: str = "memory"
    redis_url: str | None = None
    queue_stream: str = "zammad:jobs"
    queue_group: str = "zammad:jobs:workers"
    queue_consumer: str | None = None
    queue_read_block_ms: int = Field(default=1000, ge=100, le=60000)
    queue_read_count: int = Field(default=10, ge=1, le=1000)
    queue_retry_max_attempts: int = Field(default=3, ge=0, le=50)
    queue_retry_backoff_seconds: float = Field(default=2.0, gt=0)
    queue_dlq_stream: str = "zammad:jobs:dlq"
    history_stream: str = "zammad:jobs:history"
    history_retention_maxlen: int = Field(default=5000, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def _redis_required_when_backend_redis(self) -> WorkflowSettings:
        backend = (self.idempotency_backend or "").strip().lower()
        if backend not in {"memory", "redis"}:
            raise ValueError("workflow.idempotency_backend must be 'memory' or 'redis'")

        execution_backend = (self.execution_backend or "").strip().lower()
        if execution_backend not in {"inprocess", "redis_queue"}:
            raise ValueError("workflow.execution_backend must be 'inprocess' or 'redis_queue'")

        if backend == "redis" and not (self.redis_url and self.redis_url.strip()):
            raise ValueError(
                "workflow.idempotency_backend is 'redis' but workflow.redis_url is not set"
            )
        if execution_backend == "redis_queue" and not (self.redis_url and self.redis_url.strip()):
            raise ValueError(
                "workflow.execution_backend is 'redis_queue' but workflow.redis_url is not set"
            )
        return self


class FieldsSettings(_BaseSection):
    archive_path: str = "archive_path"
    archive_user_mode: str = "archive_user_mode"
    # Custom field name for archive_user in fixed mode (Bug #1/#6).
    archive_user: str = "archive_user"


class StoragePathPolicySanitizeSettings(_BaseSection):
    replace_whitespace: str = "_"
    strip_control_chars: bool = True


class StoragePathPolicySettings(_BaseSection):
    # None = no allowlist (all paths allowed); [] = no path allowed (Bug #30).
    allow_prefixes: list[str] | None = None
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
    templates_root: Path | None = None
    locale: str = "de_DE"
    timezone: str = "Europe/Berlin"
    max_articles: int = Field(default=250, ge=0)
    # fail = raise when over limit; cap_and_continue = truncate and warn (Bug #4/#10).
    article_limit_mode: str = "fail"
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
    user: str | None = None
    password: SecretStr | None = None


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
    # When true and a secret is set: requests without signature are allowed (e.g. for testing).
    # When no secret: allow_unsigned is ignored; use allow_unsigned_when_no_secret (Bug #12).
    allow_unsigned: bool = False
    # Explicit opt-in to allow /ingest when no HMAC secret is configured (insecure; dev/local only).
    allow_unsigned_when_no_secret: bool = False
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


class AdminSettings(_BaseSection):
    enabled: bool = False
    bearer_token: SecretStr | None = None
    history_limit: int = Field(default=100, ge=1, le=5000)


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
    admin: AdminSettings = Field(default_factory=AdminSettings)

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
                settings_cls,
                init_settings,
                env_settings,
                dotenv_settings,
                file_secret_settings,
            ):
                return (init_settings,)

        return _InitOnlySettings(**dict(data))

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
            get_flat_env_settings_source,
            init_settings,
            dotenv_settings,
            file_secret_settings,
        )
