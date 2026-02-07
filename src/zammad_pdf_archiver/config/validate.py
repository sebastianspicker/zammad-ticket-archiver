from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import ValidationError

from zammad_pdf_archiver.config.settings import Settings


@dataclass(frozen=True)
class ConfigValidationIssue:
    path: str
    message: str


class ConfigValidationError(ValueError):
    def __init__(self, issues: Iterable[ConfigValidationIssue]):
        self.issues = list(issues)
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        lines = ["Configuration is invalid:"]
        for issue in self.issues:
            lines.append(f"- {issue.path}: {issue.message}")
        return "\n".join(lines)


def issues_from_pydantic_error(error: ValidationError) -> list[ConfigValidationIssue]:
    issues: list[ConfigValidationIssue] = []
    for item in error.errors(include_url=False):
        loc = ".".join(str(part) for part in item.get("loc", ())) or "<root>"
        msg = item.get("msg", "Invalid value")
        issues.append(ConfigValidationIssue(path=loc, message=msg))
    return issues


def _is_local_upstream_host(host: str) -> bool:
    normalized = host.strip().lower().rstrip(".")
    if normalized in {"localhost", "localhost.localdomain"}:
        return True

    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return False

    return ip.is_loopback or ip.is_link_local or ip.is_unspecified


def _validate_upstream_host(
    *,
    url: str,
    path: str,
    allow_local_upstreams: bool,
    issues: list[ConfigValidationIssue],
) -> None:
    if allow_local_upstreams:
        return

    host = urlsplit(url).hostname
    if host and _is_local_upstream_host(host):
        issues.append(
            ConfigValidationIssue(
                path=path,
                message=(
                    "Loopback/link-local upstream hosts are blocked by default. "
                    "Set hardening.transport.allow_local_upstreams=true to override."
                ),
            )
        )


def validate_settings(settings: Settings) -> None:
    issues: list[ConfigValidationIssue] = []

    log_level = settings.observability.log_level.upper()
    allowed_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
    if log_level not in allowed_levels:
        issues.append(
            ConfigValidationIssue(
                path="observability.log_level",
                message=(
                    f"Unsupported log level {settings.observability.log_level!r} "
                    f"(allowed: {sorted(allowed_levels)})"
                ),
            )
        )

    transport = settings.hardening.transport
    if (
        str(settings.zammad.base_url).lower().startswith("http://")
        and not transport.allow_insecure_http
    ):
        issues.append(
            ConfigValidationIssue(
                path="zammad.base_url",
                message=(
                    "Plain HTTP upstream is not allowed by default. "
                    "Use https:// or set hardening.transport.allow_insecure_http=true."
                ),
            )
        )

    if not settings.zammad.verify_tls and not transport.allow_insecure_tls:
        issues.append(
            ConfigValidationIssue(
                path="zammad.verify_tls",
                message=(
                    "Disabling TLS verification is not allowed by default. "
                    "Set hardening.transport.allow_insecure_tls=true to override (not recommended)."
                ),
            )
        )

    _validate_upstream_host(
        url=str(settings.zammad.base_url),
        path="zammad.base_url",
        allow_local_upstreams=transport.allow_local_upstreams,
        issues=issues,
    )

    # Webhook auth safety: by default, /ingest must be authenticated with a configured secret.
    if not settings.hardening.webhook.allow_unsigned:
        secret = getattr(settings.zammad, "webhook_hmac_secret", None)
        legacy = getattr(settings.server, "webhook_shared_secret", None)
        secret_value = secret.get_secret_value().strip() if secret is not None else ""
        legacy_value = legacy.get_secret_value().strip() if legacy is not None else ""
        if not secret_value and not legacy_value:
            issues.append(
                ConfigValidationIssue(
                    path="zammad.webhook_hmac_secret",
                    message=(
                        "Missing webhook HMAC secret. Set WEBHOOK_HMAC_SECRET "
                        "(or hardening.webhook.allow_unsigned=true for internal/test use)."
                    ),
                )
            )

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

    # If timestamping is enabled, enforce secure transport for the TSA as well.
    if settings.signing.timestamp.enabled:
        tsa_url = settings.signing.timestamp.rfc3161.tsa_url
        if tsa_url is not None and str(tsa_url).lower().startswith("http://"):
            if not transport.allow_insecure_http:
                issues.append(
                    ConfigValidationIssue(
                        path="signing.timestamp.rfc3161.tsa_url",
                        message=(
                            "Plain HTTP TSA URL is not allowed by default. "
                            "Use https:// or set hardening.transport.allow_insecure_http=true."
                        ),
                    )
                )
        if tsa_url is not None:
            _validate_upstream_host(
                url=str(tsa_url),
                path="signing.timestamp.rfc3161.tsa_url",
                allow_local_upstreams=transport.allow_local_upstreams,
                issues=issues,
            )

    if issues:
        raise ConfigValidationError(issues)
