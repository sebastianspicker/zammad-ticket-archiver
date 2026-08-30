"""Convert configuration validation failures into stable operator diagnostics."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import ValidationError

from chronikwerk.configuration.models import Settings, canonicalize_zammad_origin
from chronikwerk.outbound import OutboundPolicyError, validate_url_policy

_MIN_AUTH_SECRET_LENGTH = 32
_PLACEHOLDER_PREFIXES = (
    "changeme",
    "example",
    "replaceme",
    "yourpassword",
    "yoursecret",
    "yourtoken",
)


@dataclass(frozen=True)
class ConfigValidationIssue:
    """Represent one stable, operator-facing configuration validation error."""

    path: str
    message: str


class ConfigValidationError(ValueError):
    """Aggregate configuration issues while preserving safe diagnostics."""

    def __init__(self, issues: Iterable[ConfigValidationIssue]):
        self.issues = list(issues)
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        lines = ["Configuration invalid:"]
        for issue in self.issues:
            lines.append(f"- {issue.path}: {issue.message}")
        return "\n".join(lines)


def _auth_secret_problem(value: str) -> str | None:
    if value != value.strip():
        return "must not contain leading or trailing whitespace"
    stripped = value.strip()
    if not stripped:
        return "is missing"
    if len(stripped) < _MIN_AUTH_SECRET_LENGTH:
        return f"must contain at least {_MIN_AUTH_SECRET_LENGTH} characters"
    normalized = "".join(character for character in stripped.lower() if character.isalnum())
    if normalized.startswith(_PLACEHOLDER_PREFIXES):
        return "must not use an example or placeholder value"
    return None


def _append_auth_secret_issue(
    issues: list[ConfigValidationIssue],
    *,
    path: str,
    value: str,
    label: str,
) -> None:
    problem = _auth_secret_problem(value)
    if problem is not None:
        issues.append(ConfigValidationIssue(path=path, message=f"{label} {problem}."))


def issues_from_pydantic_error(error: ValidationError) -> list[ConfigValidationIssue]:
    """Translate Pydantic error locations into stable configuration issue paths."""
    issues: list[ConfigValidationIssue] = []
    for item in error.errors(include_url=False):
        loc = ".".join(str(part) for part in item.get("loc", ())) or "<root>"
        msg = item.get("msg", "Invalid value")
        issues.append(ConfigValidationIssue(path=loc, message=msg))
    return issues


def _validate_webhook_auth(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    secret = settings.zammad.webhook_hmac_secret
    secret_value = secret.get_secret_value() if secret is not None else ""
    _append_auth_secret_issue(
        issues,
        path="zammad.webhook_hmac_secret",
        value=secret_value,
        label="Webhook HMAC secret",
    )


def _validate_zammad_api_token(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    token = settings.zammad.api_token.get_secret_value()
    if not token or any(character.isspace() for character in token):
        issues.append(
            ConfigValidationIssue(
                path="zammad.api_token",
                message="API token must be non-empty and contain no whitespace.",
            )
        )


def _validate_delivery_id_requirement(
    settings: Settings, issues: list[ConfigValidationIssue]
) -> None:
    if settings.hardening.webhook.require_delivery_id:
        if int(settings.workflow.delivery_id_ttl_seconds) <= 0:
            issues.append(
                ConfigValidationIssue(
                    path="workflow.delivery_id_ttl_seconds",
                    message=(
                        "hardening.webhook.require_delivery_id requires "
                        "workflow.delivery_id_ttl_seconds to be > 0."
                    ),
                )
            )


def _validate_transport(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    try:
        origin = canonicalize_zammad_origin(
            str(settings.zammad.base_url),
            allow_insecure_http=settings.hardening.transport.allow_insecure_http,
        )
        validate_url_policy(
            origin,
            allow_insecure_http=settings.hardening.transport.allow_insecure_http,
            allow_private_networks=settings.hardening.transport.allow_private_networks,
        )
    except (OutboundPolicyError, ValueError) as exc:
        issues.append(ConfigValidationIssue(path="zammad.base_url", message=str(exc)))
    if not settings.zammad.verify_tls:
        issues.append(
            ConfigValidationIssue(
                path="zammad.verify_tls",
                message="TLS verification must stay enabled.",
            )
        )
    _validate_tsa_transport(settings, issues=issues)


def _validate_tsa_transport(
    settings: Settings,
    *,
    issues: list[ConfigValidationIssue],
) -> None:
    if not settings.signing.timestamp.enabled:
        return
    tsa_url = settings.signing.timestamp.rfc3161.tsa_url
    if tsa_url is None:
        return
    tsa_url_str = str(tsa_url)
    try:
        validate_url_policy(
            tsa_url_str,
            allow_insecure_http=settings.hardening.transport.allow_insecure_http,
            allow_private_networks=settings.hardening.transport.allow_private_networks,
        )
    except OutboundPolicyError as exc:
        issues.append(
            ConfigValidationIssue(path="signing.timestamp.rfc3161.tsa_url", message=str(exc))
        )


def _validate_observability(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    if settings.observability.metrics_enabled:
        token = settings.observability.metrics_bearer_token
        _append_auth_secret_issue(
            issues,
            path="observability.metrics_bearer_token",
            value=token.get_secret_value() if token is not None else "",
            label="Metrics bearer token",
        )
    if settings.observability.history_enabled:
        token = settings.observability.history_bearer_token
        _append_auth_secret_issue(
            issues,
            path="observability.history_bearer_token",
            value=token.get_secret_value() if token is not None else "",
            label="History bearer token",
        )


def _validate_retry_auth(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    token = settings.retry_bearer_token
    if token is None:
        return
    _append_auth_secret_issue(
        issues,
        path="retry_bearer_token",
        value=token.get_secret_value(),
        label="Retry bearer token",
    )


def _validate_log_level(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    level = settings.observability.log_level.strip().upper()
    if level not in {"TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        issues.append(
            ConfigValidationIssue(
                path="observability.log_level",
                message="Unsupported log level.",
            )
        )


def _validate_admin(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    if not settings.admin.enabled:
        return
    token = settings.admin.access_token
    _append_auth_secret_issue(
        issues,
        path="admin.access_token",
        value=token.get_secret_value() if token is not None else "",
        label="Admin access token",
    )


def validate_settings(settings: Settings) -> None:
    """Validate raw configuration and raise safe, structured diagnostics."""
    issues: list[ConfigValidationIssue] = []
    _validate_webhook_auth(settings, issues)
    _validate_zammad_api_token(settings, issues)
    _validate_delivery_id_requirement(settings, issues)
    _validate_transport(settings, issues)
    _validate_observability(settings, issues)
    _validate_retry_auth(settings, issues)
    _validate_log_level(settings, issues)
    _validate_admin(settings, issues)
    if issues:
        raise ConfigValidationError(issues)
