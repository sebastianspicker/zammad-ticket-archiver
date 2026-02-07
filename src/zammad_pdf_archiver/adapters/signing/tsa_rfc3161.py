from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from asn1crypto import tsp  # type: ignore[import-untyped]
from pyhanko.sign.timestamps.api import TimeStamper
from pyhanko.sign.timestamps.common_utils import set_tsp_headers

from zammad_pdf_archiver.domain.errors import PermanentError, TransientError


@dataclass(frozen=True)
class _TsaConfig:
    url: str
    timeout_seconds: float
    ca_bundle_path: Path | None
    auth: tuple[str, str] | None
    trust_env: bool


def _resolve_tsa_settings(settings: Any) -> Any:
    signing = getattr(settings, "signing", None)
    if signing is None:
        raise ValueError("settings must have a .signing attribute")

    # Prefer the canonical config path used in this repo:
    # settings.signing.timestamp.enabled / settings.signing.timestamp.rfc3161
    timestamp = getattr(signing, "timestamp", None)
    if timestamp is not None:
        return getattr(timestamp, "rfc3161", None)

    # Backwards/alternate naming: settings.signing.tsa.enabled / settings.signing.tsa.rfc3161
    tsa = getattr(signing, "tsa", None)
    if tsa is not None:
        return getattr(tsa, "rfc3161", None)

    return None


def _load_tsa_config(settings: Any) -> _TsaConfig:
    tsa_settings = _resolve_tsa_settings(settings)
    if tsa_settings is None:
        raise PermanentError("Timestamping is enabled but TSA settings are missing")

    tsa_url = getattr(tsa_settings, "tsa_url", None)
    if tsa_url is None:
        raise PermanentError("Timestamping is enabled but TSA URL is missing")

    timeout_seconds = float(getattr(tsa_settings, "timeout_seconds", 10.0))
    ca_bundle_path = getattr(tsa_settings, "ca_bundle_path", None)
    if isinstance(ca_bundle_path, str):
        ca_bundle_path = Path(ca_bundle_path)

    user = os.getenv("TSA_USER")
    password = os.getenv("TSA_PASS")
    auth: tuple[str, str] | None
    if user is not None or password is not None:
        if not user or not password:
            raise PermanentError("TSA basic auth requires both TSA_USER and TSA_PASS")
        auth = (user, password)
    else:
        auth = None

    hardening = getattr(settings, "hardening", None)
    transport = getattr(hardening, "transport", None) if hardening is not None else None
    trust_env = bool(getattr(transport, "trust_env", False))

    return _TsaConfig(
        url=str(tsa_url),
        timeout_seconds=timeout_seconds,
        ca_bundle_path=ca_bundle_path,
        auth=auth,
        trust_env=trust_env,
    )


class _HttpxRFC3161TimeStamper(TimeStamper):
    def __init__(self, config: _TsaConfig):
        super().__init__()
        self._config = config

    async def async_request_tsa_response(self, req: tsp.TimeStampReq) -> tsp.TimeStampResp:
        headers = set_tsp_headers({})
        verify: bool | str = True
        if self._config.ca_bundle_path is not None:
            verify = str(self._config.ca_bundle_path)

        try:
            post_kwargs: dict[str, Any] = {}
            if self._config.auth is not None:
                post_kwargs["auth"] = self._config.auth
            async with httpx.AsyncClient(
                timeout=_timeouts(self._config.timeout_seconds),
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
                verify=verify,
                trust_env=self._config.trust_env,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    self._config.url,
                    content=req.dump(),
                    headers=headers,
                    **post_kwargs,
                )
        except httpx.RequestError as exc:
            raise TransientError("Error communicating with RFC3161 TSA") from exc

        if 500 <= response.status_code <= 599:
            raise TransientError(f"RFC3161 TSA returned HTTP {response.status_code}")

        if response.status_code != 200:
            raise PermanentError(f"RFC3161 TSA returned HTTP {response.status_code}")

        content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip()
        if content_type.lower() != "application/timestamp-reply":
            raise PermanentError(
                "RFC3161 TSA response is malformed (unexpected Content-Type)"
            )

        try:
            return tsp.TimeStampResp.load(response.content)
        except Exception as exc:  # noqa: BLE001 - parse errors are not retryable
            raise PermanentError("RFC3161 TSA response is not a valid TimeStampResp") from exc


def build_timestamper(settings: Any) -> TimeStamper:
    """
    Build a pyHanko-compatible RFC3161 timestamper.

    Supports optional HTTP basic auth via TSA_USER/TSA_PASS.

    Raises:
      - PermanentError for misconfiguration or non-retryable TSA responses.
      - TransientError for network issues and HTTP 5xx responses.
    """
    config = _load_tsa_config(settings)
    return _HttpxRFC3161TimeStamper(config)


def _timeouts(timeout_seconds: float) -> httpx.Timeout:
    total = float(timeout_seconds)
    connect = min(5.0, total)
    return httpx.Timeout(connect=connect, read=total, write=total, pool=connect)
