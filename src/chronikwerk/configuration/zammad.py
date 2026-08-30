"""Define the validated Zammad connection and configuration section."""

from __future__ import annotations

import re
from dataclasses import dataclass
from ipaddress import ip_address
from math import isfinite
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field, SecretStr
from pydantic.networks import AnyHttpUrl

from chronikwerk.configuration.sections import _BaseSection

ZAMMAD_CONNECTION_CONTRACT_VERSION = 2
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def canonicalize_zammad_origin(value: str, *, allow_insecure_http: bool = False) -> str:
    """Return the credential-free origin used for Zammad API requests."""
    parsed, port = _parse_zammad_origin(value)
    _validate_zammad_origin_parts(parsed, allow_insecure_http=allow_insecure_http)
    host = _canonicalize_zammad_host(parsed.hostname)
    if port == 0:
        raise ValueError("Zammad origin port must be between 1 and 65535")
    rendered_host = f"[{host}]" if ":" in host else host
    rendered_port = f":{port}" if port is not None else ""
    return f"{parsed.scheme.lower()}://{rendered_host}{rendered_port}"


def _parse_zammad_origin(value: str) -> tuple[Any, int | None]:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Zammad origin must be a valid HTTPS origin") from exc
    return parsed, port


def _validate_zammad_origin_parts(parsed: Any, *, allow_insecure_http: bool) -> None:
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Zammad origin must not include credentials")
    if not _is_zammad_origin_shape(parsed, allow_insecure_http=allow_insecure_http):
        raise ValueError("Zammad origin must be an HTTPS scheme, host, and optional port only")


def _is_zammad_origin_shape(parsed: Any, *, allow_insecure_http: bool) -> bool:
    allowed_schemes = {"https", "http"} if allow_insecure_http else {"https"}
    return (
        parsed.scheme.lower() in allowed_schemes
        and bool(parsed.hostname)
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _canonicalize_zammad_host(value: str) -> str:
    host = value.rstrip(".").lower()
    if not host:
        raise ValueError("Zammad origin must include a host")

    ip_host = _canonical_ip_host(host)
    if ip_host is not None:
        return ip_host

    host = _encode_idna_host(host)
    if not _is_valid_dns_host(host):
        raise ValueError("Zammad origin host is invalid") from None
    return host


def _canonical_ip_host(host: str) -> str | None:
    try:
        return ip_address(host).compressed
    except ValueError:
        return None


def _encode_idna_host(host: str) -> str:
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Zammad origin host is invalid") from exc


def _is_valid_dns_host(host: str) -> bool:
    labels = host.split(".")
    return (
        len(host) <= 253
        and all(_HOST_LABEL.fullmatch(label) is not None for label in labels)
        and not (len(labels) == 4 and all(label.isdigit() for label in labels))
    )


@dataclass(frozen=True, slots=True)
class ZammadConnection:
    """Immutable runtime boundary for one authenticated Zammad API connection."""

    origin: str
    api_token: SecretStr
    timeout_seconds: float = 10.0
    allow_private_origin: bool = False
    trust_environment: bool = False
    allow_insecure_http: bool = False

    def __post_init__(self) -> None:
        _validate_zammad_boolean("allow_insecure_http", self.allow_insecure_http)
        object.__setattr__(
            self,
            "origin",
            canonicalize_zammad_origin(
                self.origin,
                allow_insecure_http=self.allow_insecure_http,
            ),
        )
        _validate_zammad_api_token(self.api_token)
        object.__setattr__(self, "timeout_seconds", _validate_zammad_timeout(self.timeout_seconds))
        _validate_zammad_boolean("allow_private_origin", self.allow_private_origin)
        _validate_zammad_boolean("trust_environment", self.trust_environment)

    @property
    def api_root(self) -> str:
        """Return the fixed Zammad REST API root for this origin."""
        return f"{self.origin}/api/v1"


def _validate_zammad_api_token(value: SecretStr) -> None:
    if not isinstance(value, SecretStr):
        raise TypeError("Zammad connection api_token must be a SecretStr")
    token = value.get_secret_value()
    if not token or any(character.isspace() for character in token):
        raise ValueError("Zammad connection api_token must be non-empty and contain no whitespace")


def _validate_zammad_timeout(value: float) -> float:
    if not _is_finite_positive_number(value):
        raise ValueError("Zammad connection timeout_seconds must be a finite number greater than 0")
    return float(value)


def _is_finite_positive_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and isfinite(value)
        and value > 0
    )


def _validate_zammad_boolean(name: str, value: bool) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"Zammad connection {name} must be a boolean")


class ZammadSettings(_BaseSection):
    """Configure authenticated outbound access to the Zammad instance."""

    base_url: AnyHttpUrl
    api_token: SecretStr
    webhook_hmac_secret: SecretStr | None = None
    timeout_seconds: float = Field(default=10.0, gt=0, allow_inf_nan=False)
    verify_tls: bool = True
