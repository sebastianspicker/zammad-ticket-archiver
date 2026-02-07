from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import SecretStr

REDACTED_VALUE = "[redacted]"

_EXPLICIT_SENSITIVE_KEYS = frozenset(
    {
        # Explicit env-var style keys (can appear in logs or config dumps).
        "zammad_api_token",
        "webhook_hmac_secret",
        "pfx_password",
        "tsa_pass",
        # Common config-key style names.
        "api_token",
        "webhook_shared_secret",
        "key_password",
    }
)

_SENSITIVE_KEY_FRAGMENTS = ("password", "token", "secret", "authorization", "api_key", "apikey")

_AUTHZ_SCHEME_RE = re.compile(
    r"(?i)\b(authorization)\s*[:=]\s*(bearer|token|basic)\s+([^\s,;]+)"
)
_ZAMMAD_TOKEN_TOKEN_RE = re.compile(r"(?i)\bToken\s+token=([^\s,;]+)")
_COMMON_KV_SECRET_RE = re.compile(
    r"(?i)\b("
    r"token|api[_-]?token|access[_-]?token|refresh[_-]?token|webhook[_-]?hmac[_-]?secret|"
    r"secret|password|passwd|tsa[_-]?pass|pfx[_-]?password|key[_-]?password"
    r")\s*[:=]\s*([^\s,;]+)"
)
_COMMON_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?token|access[_-]?token|refresh[_-]?token|token|secret)=)([^&\\s]+)"
)


def scrub_secrets_in_text(text: str) -> str:
    """
    Best-effort redaction for secrets embedded in free-form text (exceptions, warnings).

    This is intentionally conservative: it targets common credential formats while trying
    to preserve readability of logs.
    """
    if not text:
        return text

    out = text

    # Authorization: Bearer <...> / Token <...> / Basic <...>
    out = _AUTHZ_SCHEME_RE.sub(r"\1: \2 " + REDACTED_VALUE, out)

    # Zammad-style auth header: "Token token=<...>"
    out = _ZAMMAD_TOKEN_TOKEN_RE.sub("Token token=" + REDACTED_VALUE, out)

    # Common key=value or key: value patterns.
    out = _COMMON_KV_SECRET_RE.sub(lambda m: f"{m.group(1)}={REDACTED_VALUE}", out)

    # Query parameters.
    out = _COMMON_QUERY_SECRET_RE.sub(lambda m: f"{m.group(1)}{REDACTED_VALUE}", out)

    return out


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower()
    if normalized in _EXPLICIT_SENSITIVE_KEYS:
        return True
    if normalized.endswith("_pass"):
        return True
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _redact_value(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return REDACTED_VALUE
    if isinstance(value, str):
        return scrub_secrets_in_text(value)
    if isinstance(value, Mapping):
        return redact_settings_dict(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    return value


def redact_settings_dict(data: Mapping[str, Any]) -> dict[str, Any]:
    """
    Returns a deep-redacted copy of `data` (does not mutate input).

    Redaction rules:
    - Any value under a sensitive key is replaced with `REDACTED_VALUE`.
    - Any `pydantic.SecretStr` value is replaced with `REDACTED_VALUE` even if the key is not known.
    """
    scrubbed: dict[str, Any] = {}
    for key, value in data.items():
        if _is_sensitive_key(str(key)):
            scrubbed[str(key)] = REDACTED_VALUE
        else:
            scrubbed[str(key)] = _redact_value(value)
    return scrubbed
