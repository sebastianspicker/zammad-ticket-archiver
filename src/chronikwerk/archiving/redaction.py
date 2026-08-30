"""Redact credential-shaped values before archive errors reach ticket notes."""

from __future__ import annotations

import re

_REDACTED = "[redacted]"
_KEY_VALUE = re.compile(
    r"(?i)\b(token|api[_-]?token|access[_-]?token|refresh[_-]?token|apikey|api_key|"
    r"client[_-]?(?:secret|token)|password|secret|passwd|authorization|private[_-]?key|"
    r"webhook[_-]?hmac[_-]?secret|pfx[_-]?password|tsa[_-]?pass)(\s*[:=]\s*)([^\s,;]+)"
)
_AUTHORIZATION = re.compile(r"(?i)\b(authorization)\s*[:=]\s*(bearer|token|basic)\s+([^\s,;]+)")
_ZAMMAD_TOKEN = re.compile(r"(?i)\bToken\s+token=([^\s,;]+)")
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:api[_-]?token|access[_-]?token|refresh[_-]?token|client[_-]?(?:secret|token)|"
    r"private[_-]?key|token|secret)=)([^&\s]+)"
)
_CONNECTION_CREDENTIALS = re.compile(r"([a-z][a-z0-9+.-]*://)([^/@\s]+)@", re.IGNORECASE)


def scrub_secrets_in_text(text: str) -> str:
    """Return a readable error message without common embedded credentials."""
    if not text:
        return text
    value = _AUTHORIZATION.sub(r"\1: \2 " + _REDACTED, text)
    value = _ZAMMAD_TOKEN.sub("Token token=" + _REDACTED, value)
    value = _KEY_VALUE.sub(lambda match: f"{match.group(1)}={_REDACTED}", value)
    value = _QUERY_SECRET.sub(lambda match: f"{match.group(1)}{_REDACTED}", value)
    return _CONNECTION_CREDENTIALS.sub(rf"\1{_REDACTED}@", value)
