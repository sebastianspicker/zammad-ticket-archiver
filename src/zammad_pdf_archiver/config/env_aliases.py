"""Environment variable aliases for backward compatibility.

This module handles legacy environment variable names and provides
DeprecationWarnings when they are used.
"""
from __future__ import annotations

import os
import warnings
from collections.abc import Iterable, Mapping
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
    warnings.warn(
        f"Environment variable '{old_name}' is deprecated. Use '{new_name}' instead. "
        f"Support for '{old_name}' will be removed in a future version.",
        DeprecationWarning,
        stacklevel=3,
    )


def _set_nested(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    node = data
    for part in path[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[path[-1]] = value


def _apply_alias_mappings(
    env: Mapping[str, str],
    data: dict[str, Any],
    mappings: Iterable[tuple[str, tuple[str, ...]]],
) -> None:
    for env_name, path in mappings:
        value = env.get(env_name)
        if value:
            _set_nested(data, path, value)


def _apply_deprecated_aliases(
    env: Mapping[str, str],
    data: dict[str, Any],
    deprecated_mappings: Iterable[tuple[str, str, tuple[str, ...]]],
) -> None:
    for old_name, new_name, path in deprecated_mappings:
        old_value = env.get(old_name)
        if not old_value:
            continue
        if env.get(new_name):
            continue
        _warn_deprecated_env_var(old_name, new_name)
        _set_nested(data, path, old_value)


_CANONICAL_MAPPINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Server
    ("SERVER_HOST", ("server", "host")),
    ("SERVER_PORT", ("server", "port")),
    ("WEBHOOK_SHARED_SECRET", ("server", "webhook_shared_secret")),
    # Zammad
    ("ZAMMAD_BASE_URL", ("zammad", "base_url")),
    ("ZAMMAD_API_TOKEN", ("zammad", "api_token")),
    ("WEBHOOK_HMAC_SECRET", ("zammad", "webhook_hmac_secret")),
    ("ZAMMAD_TIMEOUT_SECONDS", ("zammad", "timeout_seconds")),
    ("ZAMMAD_VERIFY_TLS", ("zammad", "verify_tls")),
    # Workflow
    ("WORKFLOW_TRIGGER_TAG", ("workflow", "trigger_tag")),
    ("WORKFLOW_REQUIRE_TAG", ("workflow", "require_tag")),
    ("WORKFLOW_DELIVERY_ID_TTL_SECONDS", ("workflow", "delivery_id_ttl_seconds")),
    ("WORKFLOW_EXECUTION_BACKEND", ("workflow", "execution_backend")),
    ("IDEMPOTENCY_BACKEND", ("workflow", "idempotency_backend")),
    ("REDIS_URL", ("workflow", "redis_url")),
    ("WORKFLOW_QUEUE_STREAM", ("workflow", "queue_stream")),
    ("WORKFLOW_QUEUE_GROUP", ("workflow", "queue_group")),
    ("WORKFLOW_QUEUE_CONSUMER", ("workflow", "queue_consumer")),
    ("WORKFLOW_QUEUE_READ_BLOCK_MS", ("workflow", "queue_read_block_ms")),
    ("WORKFLOW_QUEUE_READ_COUNT", ("workflow", "queue_read_count")),
    ("WORKFLOW_QUEUE_RETRY_MAX_ATTEMPTS", ("workflow", "queue_retry_max_attempts")),
    ("WORKFLOW_QUEUE_RETRY_BACKOFF_SECONDS", ("workflow", "queue_retry_backoff_seconds")),
    ("WORKFLOW_QUEUE_DLQ_STREAM", ("workflow", "queue_dlq_stream")),
    ("WORKFLOW_HISTORY_STREAM", ("workflow", "history_stream")),
    ("WORKFLOW_HISTORY_RETENTION_MAXLEN", ("workflow", "history_retention_maxlen")),
    # Fields
    ("FIELDS_ARCHIVE_PATH", ("fields", "archive_path")),
    ("FIELDS_ARCHIVE_USER_MODE", ("fields", "archive_user_mode")),
    ("FIELDS_ARCHIVE_USER", ("fields", "archive_user")),
    # Storage
    ("STORAGE_ROOT", ("storage", "root")),
    ("STORAGE_ATOMIC_WRITE", ("storage", "atomic_write")),
    ("STORAGE_FSYNC", ("storage", "fsync")),
    # PDF
    ("PDF_TEMPLATE_VARIANT", ("pdf", "template_variant")),
    ("TEMPLATES_ROOT", ("pdf", "templates_root")),
    ("PDF_LOCALE", ("pdf", "locale")),
    ("PDF_TIMEZONE", ("pdf", "timezone")),
    ("PDF_MAX_ARTICLES", ("pdf", "max_articles")),
    ("PDF_ARTICLE_LIMIT_MODE", ("pdf", "article_limit_mode")),
    ("PDF_INCLUDE_ATTACHMENT_BINARY", ("pdf", "include_attachment_binary")),
    ("PDF_MAX_ATTACHMENT_BYTES_PER_FILE", ("pdf", "max_attachment_bytes_per_file")),
    ("PDF_MAX_TOTAL_ATTACHMENT_BYTES", ("pdf", "max_total_attachment_bytes")),
    # Signing
    ("SIGNING_ENABLED", ("signing", "enabled")),
    ("SIGNING_PFX_PATH", ("signing", "pfx_path")),
    ("SIGNING_PFX_PASSWORD", ("signing", "pfx_password")),
    ("SIGNING_CERT_PATH", ("signing", "pades", "cert_path")),
    ("SIGNING_KEY_PATH", ("signing", "pades", "key_path")),
    ("SIGNING_KEY_PASSWORD", ("signing", "pades", "key_password")),
    ("SIGNING_REASON", ("signing", "pades", "reason")),
    ("SIGNING_LOCATION", ("signing", "pades", "location")),
    ("TSA_ENABLED", ("signing", "timestamp", "enabled")),
    ("TSA_URL", ("signing", "timestamp", "rfc3161", "tsa_url")),
    ("TSA_TIMEOUT_SECONDS", ("signing", "timestamp", "rfc3161", "timeout_seconds")),
    ("TSA_CA_BUNDLE_PATH", ("signing", "timestamp", "rfc3161", "ca_bundle_path")),
    ("TSA_USER", ("signing", "timestamp", "rfc3161", "user")),
    ("TSA_PASS", ("signing", "timestamp", "rfc3161", "password")),
    # Observability
    ("LOG_LEVEL", ("observability", "log_level")),
    ("LOG_FORMAT", ("observability", "log_format")),
    ("LOG_JSON", ("observability", "json_logs")),
    ("METRICS_ENABLED", ("observability", "metrics_enabled")),
    ("METRICS_BEARER_TOKEN", ("observability", "metrics_bearer_token")),
    ("HEALTHZ_OMIT_VERSION", ("observability", "healthz_omit_version")),
    # Hardening
    ("RATE_LIMIT_ENABLED", ("hardening", "rate_limit", "enabled")),
    ("RATE_LIMIT_RPS", ("hardening", "rate_limit", "rps")),
    ("RATE_LIMIT_BURST", ("hardening", "rate_limit", "burst")),
    ("RATE_LIMIT_INCLUDE_METRICS", ("hardening", "rate_limit", "include_metrics")),
    ("RATE_LIMIT_CLIENT_KEY_HEADER", ("hardening", "rate_limit", "client_key_header")),
    ("MAX_BODY_BYTES", ("hardening", "body_size_limit", "max_bytes")),
    ("HARDENING_WEBHOOK_ALLOW_UNSIGNED", ("hardening", "webhook", "allow_unsigned")),
    (
        "HARDENING_WEBHOOK_ALLOW_UNSIGNED_WHEN_NO_SECRET",
        ("hardening", "webhook", "allow_unsigned_when_no_secret"),
    ),
    ("HARDENING_WEBHOOK_REQUIRE_DELIVERY_ID", ("hardening", "webhook", "require_delivery_id")),
    ("HARDENING_TRANSPORT_TRUST_ENV", ("hardening", "transport", "trust_env")),
    (
        "HARDENING_TRANSPORT_ALLOW_INSECURE_HTTP",
        ("hardening", "transport", "allow_insecure_http"),
    ),
    (
        "HARDENING_TRANSPORT_ALLOW_INSECURE_TLS",
        ("hardening", "transport", "allow_insecure_tls"),
    ),
    (
        "HARDENING_TRANSPORT_ALLOW_LOCAL_UPSTREAMS",
        ("hardening", "transport", "allow_local_upstreams"),
    ),
    # Admin
    ("ADMIN_ENABLED", ("admin", "enabled")),
    ("ADMIN_BEARER_TOKEN", ("admin", "bearer_token")),
    ("ADMIN_HISTORY_LIMIT", ("admin", "history_limit")),
)

_DEPRECATED_VALUE_MAPPINGS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ZAMMAD_URL", "ZAMMAD_BASE_URL", ("zammad", "base_url")),
    ("TEMPLATE_VARIANT", "PDF_TEMPLATE_VARIANT", ("pdf", "template_variant")),
    ("RENDER_LOCALE", "PDF_LOCALE", ("pdf", "locale")),
    ("RENDER_TIMEZONE", "PDF_TIMEZONE", ("pdf", "timezone")),
    (
        "OBSERVABILITY_METRICS_ENABLED",
        "METRICS_ENABLED",
        ("observability", "metrics_enabled"),
    ),
)


def get_flat_env_settings_source() -> dict[str, Any]:
    env = os.environ
    data: dict[str, Any] = {}

    _apply_alias_mappings(env, data, _CANONICAL_MAPPINGS)
    _apply_deprecated_aliases(env, data, _DEPRECATED_VALUE_MAPPINGS)

    return data
