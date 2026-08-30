"""Redact secrets before configuration is logged or returned to operators."""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from typing import Any

from pydantic import SecretStr

REDACTED_VALUE = "[redacted]"

_SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "token",
    "secret",
    "authorization",
    "api_key",
    "apikey",
    "redis_url",
    "private_key",
    "private-key",
)

_AUTHZ_SCHEME_RE = re.compile(r"(?i)\b(authorization)\s*[:=]\s*(bearer|token|basic)\s+([^\s,;]+)")
_ZAMMAD_TOKEN_TOKEN_RE = re.compile(r"(?i)\bToken\s+token=([^\s,;]+)")
_COMMON_KV_SECRET_RE = re.compile(
    r"(?i)\b("
    r"token|api[_-]?token|access[_-]?token|refresh[_-]?token|webhook[_-]?hmac[_-]?secret|"
    r"client[_-]?(?:secret|token)|private[_-]?key|secret|password|passwd|tsa[_-]?pass|"
    r"pfx[_-]?password|key[_-]?password"
    r")\s*[:=]\s*([^\s,;]+)"
)
_COMMON_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?token|access[_-]?token|refresh[_-]?token|client[_-]?(?:secret|token)|"
    r"private[_-]?key|token|secret)=)([^&\\s]+)"
)
_QUOTED_KEY_RE = r"""(?P<key>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')"""
_QUOTED_VALUE_RE = r"""(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')"""
# Bug #22: JSON/Python-dict-style quoted keys and values.
_JSON_STYLE_SECRET_RE = re.compile(
    _QUOTED_KEY_RE + r"(?P<separator>\s*:\s*)" + _QUOTED_VALUE_RE,
    re.DOTALL,
)
_UNTERMINATED_DOUBLE_QUOTED_SECRET_RE = re.compile(
    _QUOTED_KEY_RE + r"""(?P<separator>\s*:\s*)(?P<value_quote>")(?:\\.|[^"\\])*\\?\Z""",
    re.DOTALL,
)
_UNTERMINATED_SINGLE_QUOTED_SECRET_RE = re.compile(
    _QUOTED_KEY_RE + r"(?P<separator>\s*:\s*)(?P<value_quote>')(?:\\.|[^'\\])*\\?\Z",
    re.DOTALL,
)
_QUOTED_KV_SECRET_RE = re.compile(
    r"""(?i)\b(token|api[_-]?token|access[_-]?token|refresh[_-]?token|apikey|api_key|"""
    r"""client[_-]?(?:secret|token)|password|secret|passwd|authorization|private[_-]?key|"""
    r"""webhook[_-]?hmac[_-]?secret|pfx[_-]?password|tsa[_-]?pass)(\s*[:=]\s*)"""
    r"""((?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'))""",
    re.DOTALL,
)
# Bug #23: env-var style lines (e.g. ZAMMAD__API_TOKEN=..., SIGNING_PFX_PASSWORD=...).
_ENV_VAR_SECRET_RE = re.compile(
    r"(?im)^([A-Za-z_][A-Za-z0-9_]*(?:API[_-]?TOKEN|TOKEN|PASSWORD|SECRET|"
    r"PASSWD|PFX_PASS|TSA_PASS|PRIVATE[_-]?KEY)\s*=\s*)([^\s#]+)"
)
# Bug #42: api_key/apikey in free-form key=value (explicit pattern).
_API_KEY_KV_RE = re.compile(r"(?i)\b(api[_-]?key|apikey)\s*[:=]\s*([^\s,;]+)")
_CONN_URL_CRED_RE = re.compile(r"([a-z][a-z0-9+.-]*://)([^/@\s]+)@", flags=re.IGNORECASE)


def scrub_secrets_in_text(text: str) -> str:
    """
    Best-effort redaction for secrets embedded in free-form text (exceptions, warnings).

    This is intentionally conservative: it targets common credential formats while trying
    to preserve readability of logs.
    """
    if not text:
        return text

    out = text

    # Quoted mapping representations, including escaped JSON/Python repr keys and values.
    out = _JSON_STYLE_SECRET_RE.sub(_redact_quoted_mapping_pair, out)
    out = _QUOTED_KV_SECRET_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)[0]}{REDACTED_VALUE}{m.group(3)[-1]}",
        out,
    )
    out = _UNTERMINATED_DOUBLE_QUOTED_SECRET_RE.sub(_redact_unterminated_quoted_mapping, out)
    out = _UNTERMINATED_SINGLE_QUOTED_SECRET_RE.sub(_redact_unterminated_quoted_mapping, out)

    # Authorization: Bearer <...> / Token <...> / Basic <...>
    out = _AUTHZ_SCHEME_RE.sub(r"\1: \2 " + REDACTED_VALUE, out)

    # Zammad-style auth header: "Token token=<...>"
    out = _ZAMMAD_TOKEN_TOKEN_RE.sub("Token token=" + REDACTED_VALUE, out)

    # Common key=value or key: value patterns.
    out = _COMMON_KV_SECRET_RE.sub(lambda m: f"{m.group(1)}={REDACTED_VALUE}", out)

    # Bug #42: api_key / apikey in free-form text.
    out = _API_KEY_KV_RE.sub(lambda m: f"{m.group(1)}={REDACTED_VALUE}", out)

    # Bug #23: env-var style lines (e.g. ZAMMAD__API_TOKEN=...).
    out = _ENV_VAR_SECRET_RE.sub(lambda m: f"{m.group(1)}{REDACTED_VALUE}", out)

    # Query parameters.
    out = _COMMON_QUERY_SECRET_RE.sub(lambda m: f"{m.group(1)}{REDACTED_VALUE}", out)

    # Connection-string URLs with embedded credentials .
    out = _CONN_URL_CRED_RE.sub(rf"\1{REDACTED_VALUE}@", out)

    return out


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower()
    if normalized.endswith("_pass"):
        return True
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _decode_quoted_key(quoted_key: str) -> str:
    try:
        decoded = ast.literal_eval(quoted_key)
    except SyntaxError, ValueError:
        return re.sub(r"\\(.)", r"\1", quoted_key[1:-1])
    return decoded if isinstance(decoded, str) else quoted_key[1:-1]


def _redact_quoted_mapping_pair(match: re.Match[str]) -> str:
    if not _is_sensitive_key(_decode_quoted_key(match["key"])):
        return match[0]
    value = match["value"]
    return f"{match['key']}{match['separator']}{value[0]}{REDACTED_VALUE}{value[-1]}"


def _redact_unterminated_quoted_mapping(match: re.Match[str]) -> str:
    if not _is_sensitive_key(_decode_quoted_key(match["key"])):
        return match[0]
    return f"{match['key']}{match['separator']}{match['value_quote']}{REDACTED_VALUE}"


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
