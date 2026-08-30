"""Define non-signing configuration sections."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from chronikwerk.i18n import normalize_locale


class _BaseSection(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerSettings(_BaseSection):
    """Configure the ASGI bind address and port."""

    # 0.0.0.0 is the standard bind address for containerized services so the
    # process is reachable from outside the container.  A reverse proxy (e.g.
    # nginx, Traefik, cloud load balancer) should handle external access,
    # TLS termination, and IP filtering.
    # Container bind; proxy/firewall owns exposure.
    host: str = "0.0.0.0"  # nosec B104
    port: int = Field(default=8080, ge=1, le=65535)


class WorkflowSettings(_BaseSection):
    """Configure which ticket state changes trigger archival."""

    trigger_tag: str = "pdf:sign"
    require_tag: bool = True
    acknowledge_on_success: bool = True
    delivery_id_ttl_seconds: int = Field(default=3600, ge=0)


class FieldsSettings(_BaseSection):
    """Configure Zammad custom fields that influence archive ownership."""

    archive_path: str = "archive_path"
    archive_user_mode: str = "archive_user_mode"
    # Custom field name for archive_user in fixed mode (Bug #1/#6).
    archive_user: str = "archive_user"


class StorageSettings(_BaseSection):
    """Configure durable filesystem storage for produced PDFs."""

    root: Path
    fsync: bool = True
    filename_pattern: str = "Ticket-{ticket_number}_{timestamp_utc}.pdf"

    @field_validator("root")
    @classmethod
    def _expand_root(cls, value: Path) -> Path:
        return value.expanduser()


class PdfSettings(_BaseSection):
    """Configure localization and article limits for PDF rendering."""

    locale: str = "de-DE"
    timezone: str = "Europe/Berlin"
    max_articles: int = Field(default=250, ge=0)
    # fail = fail the ticket; cap_and_continue = truncate and warn.
    article_limit_mode: str = "fail"

    @field_validator("locale")
    @classmethod
    def _normalize_locale(cls, value: str) -> str:
        return normalize_locale(value)

    @field_validator("article_limit_mode")
    @classmethod
    def _validate_article_limit_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized in {"fail", "cap_and_continue"}:
            return normalized
        raise ValueError("pdf.article_limit_mode must be 'fail' or 'cap_and_continue'")


class ObservabilitySettings(_BaseSection):
    """Configure logging, metrics, and optional operator diagnostics."""

    log_level: str = "INFO"
    log_format: str | None = None  # json|human
    metrics_enabled: bool = False
    # When set, GET /metrics requires Authorization: Bearer <this token> (constant-time compare).
    metrics_bearer_token: SecretStr | None = None
    history_enabled: bool = False
    history_bearer_token: SecretStr | None = None
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
    """Configure request-rate limits and trusted client identity headers."""

    enabled: bool = True
    rps: float = Field(default=5.0, ge=0, le=10_000)
    burst: int = Field(default=10, ge=1, le=10_000)
    include_metrics: bool = False
    # When set (e.g. "X-Forwarded-For"), rate limit key is taken from this header (first value).
    # Trust proxy to set it; use with care.
    client_key_header: str | None = None


class BodySizeLimitSettings(_BaseSection):
    """Configure the maximum accepted HTTP request body size."""

    # 0 selects the middleware's non-disableable absolute safety cap.
    max_bytes: int = Field(default=1024 * 1024, ge=0)
    # Whole-body deadline, including slow/chunked uploads.
    timeout_seconds: float = Field(default=10.0, gt=0, le=300)


class AdmissionSettings(_BaseSection):
    """Bounds for process-local background ticket work."""

    max_pending: int = Field(default=100, ge=0, le=10_000)
    max_running: int = Field(default=4, ge=1, le=1_000)
    shutdown_timeout_seconds: float = Field(default=5.0, gt=0, le=300)


class AdminSettings(_BaseSection):
    """Feature-flagged, single-user administration application."""

    enabled: bool = False
    access_token: SecretStr | None = None
    state_dir: Path = Path("/var/lib/chronikwerk/admin")
    session_idle_seconds: int = Field(default=1800, ge=60, le=86_400)
    session_absolute_seconds: int = Field(default=28_800, ge=300, le=604_800)
    cookie_secure: bool = True
    default_locale: str = "de-DE"

    @field_validator("state_dir")
    @classmethod
    def _expand_state_dir(cls, value: Path) -> Path:
        return value.expanduser()

    @field_validator("default_locale")
    @classmethod
    def _normalize_default_locale(cls, value: str) -> str:
        return normalize_locale(value)

    @model_validator(mode="after")
    def _validate_session_lifetimes(self) -> AdminSettings:
        if self.session_idle_seconds > self.session_absolute_seconds:
            raise ValueError("admin.session_idle_seconds must not exceed session_absolute_seconds")
        return self


class WebhookHardeningSettings(_BaseSection):
    """Configure mandatory webhook authentication controls."""

    # When enabled, /ingest requires X-Zammad-Delivery replay TTL > 0.
    require_delivery_id: bool = False


class TransportHardeningSettings(_BaseSection):
    """Configure outbound HTTPS and proxy-trust restrictions."""

    # When true, allow httpx to read HTTP_PROXY/HTTPS_PROXY/NO_PROXY.
    trust_env: bool = False
    allow_insecure_http: bool = False
    allow_private_networks: bool = False


class HardeningSettings(_BaseSection):
    """Group runtime hardening controls applied during startup."""

    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    body_size_limit: BodySizeLimitSettings = Field(default_factory=BodySizeLimitSettings)
    webhook: WebhookHardeningSettings = Field(default_factory=WebhookHardeningSettings)
    transport: TransportHardeningSettings = Field(default_factory=TransportHardeningSettings)
