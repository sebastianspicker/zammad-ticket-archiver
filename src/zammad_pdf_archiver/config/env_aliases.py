"""Environment variable aliases for backward compatibility.

This module handles legacy environment variable names and provides
deprecation warnings when they are used.

Deprecated aliases (will be removed in a future version):
- ZAMMAD_URL → ZAMMAD_BASE_URL
- TEMPLATE_VARIANT → PDF_TEMPLATE_VARIANT
- RENDER_LOCALE → PDF_LOCALE
- RENDER_TIMEZONE → PDF_TIMEZONE
"""
from __future__ import annotations

import os
import warnings
from typing import Any

# Mapping of deprecated env vars to their canonical names
_DEPRECATED_ALIASES: dict[str, str] = {
    "ZAMMAD_URL": "ZAMMAD_BASE_URL",
    "TEMPLATE_VARIANT": "PDF_TEMPLATE_VARIANT",
    "RENDER_LOCALE": "PDF_LOCALE",
    "RENDER_TIMEZONE": "PDF_TIMEZONE",
    "OBSERVABILITY_METRICS_ENABLED": "METRICS_ENABLED",
}


def _warn_deprecated_env_var(old_name: str, new_name: str) -> None:
    """Emit a deprecation warning for a legacy env var.
    
    Args:
        old_name: Deprecated environment variable name
        new_name: Canonical environment variable name
    """
    warnings.warn(
        f"Environment variable '{old_name}' is deprecated. Use '{new_name}' instead. "
        f"Support for '{old_name}' will be removed in a future version.",
        DeprecationWarning,
        stacklevel=3,
    )


def get_flat_env_settings_source() -> dict[str, Any]:
    """Build settings dict from flat environment variable aliases.
    
    This function reads environment variables and maps them to the
    nested settings structure. It also emits deprecation warnings
    for legacy variable names.
    
    Returns:
        Dictionary with nested settings structure
    """
    env = os.environ
    data: dict[str, Any] = {}
    
    # Check for deprecated aliases and warn
    for old_name, new_name in _DEPRECATED_ALIASES.items():
        if old_name in env and new_name not in env:
            _warn_deprecated_env_var(old_name, new_name)

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
    elif (value := env.get("ZAMMAD_URL")):  # Deprecated alias
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
    if (value := env.get("FIELDS_ARCHIVE_USER")):
        data.setdefault("fields", {})["archive_user"] = value

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
    elif (value := env.get("TEMPLATE_VARIANT")):  # Deprecated alias
        data.setdefault("pdf", {})["template_variant"] = value
    if (value := env.get("TEMPLATES_ROOT")):
        data.setdefault("pdf", {})["templates_root"] = value
    if (value := env.get("PDF_LOCALE")):
        data.setdefault("pdf", {})["locale"] = value
    elif (value := env.get("RENDER_LOCALE")):  # Deprecated alias
        data.setdefault("pdf", {})["locale"] = value
    if (value := env.get("PDF_TIMEZONE")):
        data.setdefault("pdf", {})["timezone"] = value
    elif (value := env.get("RENDER_TIMEZONE")):  # Deprecated alias
        data.setdefault("pdf", {})["timezone"] = value
    if (value := env.get("PDF_MAX_ARTICLES")):
        data.setdefault("pdf", {})["max_articles"] = value
    if (value := env.get("PDF_ARTICLE_LIMIT_MODE")):
        data.setdefault("pdf", {})["article_limit_mode"] = value
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
    if (value := env.get("TSA_USER")):
        data.setdefault("signing", {}).setdefault("timestamp", {}).setdefault("rfc3161", {})[
            "user"
        ] = value
    if (value := env.get("TSA_PASS")):
        data.setdefault("signing", {}).setdefault("timestamp", {}).setdefault("rfc3161", {})[
            "password"
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
    if (value := env.get("OBSERVABILITY_METRICS_ENABLED")):  # Deprecated alias
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
    if (value := env.get("HARDENING_WEBHOOK_ALLOW_UNSIGNED_WHEN_NO_SECRET")):
        data.setdefault("hardening", {}).setdefault("webhook", {})[
            "allow_unsigned_when_no_secret"
        ] = value
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
