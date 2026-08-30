"""Request and validate RFC 3161 timestamps for archived PDF content."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
from asn1crypto import tsp
from pyhanko.sign.timestamps.api import TimeStamper
from pyhanko.sign.timestamps.common_utils import set_tsp_headers

from chronikwerk.documents.options import TimestampOptions
from chronikwerk.failures import PermanentError, TransientError
from chronikwerk.outbound import (
    OutboundPolicyPermanentError,
    OutboundPolicyTransientError,
    validate_url_policy_async,
)
from chronikwerk.zammad.http import (
    ResponseBodyTooLargeError,
    UnsupportedResponseEncodingError,
    buffered_response,
    pin_request_url,
    read_response_body_limited,
    timeouts_for,
)

_MAX_TSA_RESPONSE_BODY_BYTES = 1024 * 1024


@dataclass(frozen=True)
class _TsaConfig:
    url: str
    timeout_seconds: float
    ca_bundle_path: Path | None
    auth: tuple[str, str] | None
    trust_env: bool
    allow_insecure_http: bool = False
    allow_private_networks: bool = False


def _load_tsa_config(timestamp: TimestampOptions) -> _TsaConfig:
    tsa_url = timestamp.tsa_url
    if tsa_url is None:
        raise PermanentError("Timestamping is enabled but TSA URL is missing")

    timeout_seconds = float(timestamp.timeout_seconds)
    ca_bundle_path = timestamp.ca_bundle_path

    user = timestamp.user
    password = timestamp.password

    auth: tuple[str, str] | None
    if user or password:
        if not user or not password:
            raise PermanentError("TSA basic auth requires both user and password in settings")
        auth = (user, password)
    else:
        auth = None

    return _TsaConfig(
        url=str(tsa_url),
        timeout_seconds=timeout_seconds,
        ca_bundle_path=ca_bundle_path,
        auth=auth,
        trust_env=timestamp.trust_env,
        allow_insecure_http=timestamp.allow_insecure_http,
        allow_private_networks=timestamp.allow_private_networks,
    )


class _HttpxRFC3161TimeStamper(TimeStamper):
    def __init__(self, config: _TsaConfig):
        super().__init__()
        self._config = config

    def _verify_value(self) -> bool | str:
        verify: bool | str = True
        if self._config.ca_bundle_path is not None:
            verify = str(self._config.ca_bundle_path)
        return verify

    async def _post_tsa_request(self, req: tsp.TimeStampReq) -> httpx.Response:
        try:
            resolved_address = await validate_url_policy_async(
                self._config.url,
                allow_insecure_http=self._config.allow_insecure_http,
                allow_private_networks=self._config.allow_private_networks,
                timeout_seconds=min(5.0, self._config.timeout_seconds),
            )
            request_url, pin_headers, extensions = pin_request_url(
                httpx.URL(self._config.url),
                resolved_address,
            )
            auth: tuple[str | bytes, str | bytes] | None = self._config.auth

            async with httpx.AsyncClient(
                timeout=timeouts_for(self._config.timeout_seconds),
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
                verify=self._verify_value(),
                trust_env=self._config.trust_env,
                follow_redirects=False,
                auth=auth,
            ) as client:
                async with client.stream(
                    "POST",
                    request_url,
                    content=req.dump(),
                    headers=set_tsp_headers({"Accept-Encoding": "identity", **pin_headers}),
                    extensions=extensions or None,
                ) as streamed_response:
                    self._validate_http_response(streamed_response)
                    try:
                        content = await read_response_body_limited(
                            streamed_response,
                            max_bytes=_MAX_TSA_RESPONSE_BODY_BYTES,
                        )
                    except ResponseBodyTooLargeError as exc:
                        raise PermanentError(
                            "RFC3161 TSA response exceeds the "
                            f"{_MAX_TSA_RESPONSE_BODY_BYTES}-byte limit"
                        ) from exc
                    except UnsupportedResponseEncodingError as exc:
                        raise PermanentError(
                            "RFC3161 TSA returned a compressed response despite "
                            "Accept-Encoding: identity"
                        ) from exc
                    return buffered_response(streamed_response, content)
        except (httpx.RequestError, OutboundPolicyTransientError) as exc:
            raise TransientError("Error communicating with RFC3161 TSA") from exc
        except OutboundPolicyPermanentError as exc:
            raise PermanentError(str(exc)) from exc

    @staticmethod
    def _validate_http_response(response: httpx.Response) -> None:
        if 500 <= response.status_code <= 599:
            raise TransientError(f"RFC3161 TSA returned HTTP {response.status_code}")

        if response.status_code != 200:
            raise PermanentError(f"RFC3161 TSA returned HTTP {response.status_code}")

        content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip()
        if content_type.lower() != "application/timestamp-reply":
            raise PermanentError("RFC3161 TSA response is malformed (unexpected Content-Type)")

    @staticmethod
    def _parse_tsa_response(response: httpx.Response) -> tsp.TimeStampResp:
        try:
            return tsp.TimeStampResp.load(response.content)
        except (TypeError, ValueError) as exc:
            raise PermanentError("RFC3161 TSA response is not a valid TimeStampResp") from exc

    @staticmethod
    def _validate_tsa_status(tsa_resp: tsp.TimeStampResp) -> None:
        status_info = tsa_resp["status"]
        status_value = status_info["status"].native
        accepted_statuses = {"granted", "granted_with_mods"}
        if status_value not in accepted_statuses:
            status_string = ""
            try:
                status_string = status_info["status_string"].native or ""
            except KeyError, TypeError, ValueError:
                pass
            raise PermanentError(
                f"RFC3161 TSA rejected the request: status={status_value!r}"
                f"{f' ({status_string})' if status_string else ''}"
            )

    @staticmethod
    def _validate_nonce(req: tsp.TimeStampReq, tsa_resp: tsp.TimeStampResp) -> None:
        req_nonce = req["nonce"].native if req["nonce"].contents is not None else None
        tst_info = tsa_resp["time_stamp_token"]["content"]["encap_content_info"]["content"].parsed
        resp_nonce = tst_info["nonce"].native if tst_info["nonce"].contents is not None else None
        if req_nonce is not None and req_nonce != resp_nonce:
            raise PermanentError(
                f"RFC3161 TSA response nonce mismatch: expected {req_nonce}, got {resp_nonce}"
            )

    async def async_request_tsa_response(self, req: tsp.TimeStampReq) -> tsp.TimeStampResp:
        """Request a timestamp response without blocking the caller event loop."""
        response = await self._post_tsa_request(req)
        tsa_resp = self._parse_tsa_response(response)
        self._validate_tsa_status(tsa_resp)
        self._validate_nonce(req, tsa_resp)
        return tsa_resp


def build_timestamper(timestamp: TimestampOptions) -> TimeStamper:
    """
    Build a pyHanko-compatible RFC3161 timestamper.

    Supports optional HTTP basic auth via TSA_USER/TSA_PASS.

    Raises:
      - PermanentError for misconfiguration or non-retryable TSA responses.
      - TransientError for network issues and HTTP 5xx responses.
    """
    config = _load_tsa_config(timestamp)
    return _HttpxRFC3161TimeStamper(config)
