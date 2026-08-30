"""Shared request and response assertions for HTTP security tests."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from chronikwerk.configuration.models import Settings
from chronikwerk.web.app import create_app
from tests.support.hmac_test_helpers import sign_body
from tests.support.settings_factory import make_settings


def ingest_headers(
    *,
    signature: str,
    delivery_id: str | None = None,
) -> dict[str, str]:
    """Build the common signed-ingest headers, optionally binding a delivery ID."""
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature": signature,
    }
    if delivery_id is not None:
        headers["X-Zammad-Delivery"] = delivery_id
    return headers


def post_ingest(
    client: TestClient,
    body: bytes,
    signature: str,
    *,
    delivery_id: str | None = None,
):
    """Post a raw body to ingest with the standard HMAC headers."""
    return client.post(
        "/ingest",
        content=body,
        headers=ingest_headers(signature=signature, delivery_id=delivery_id),
    )


def post_signed_json(
    client: Any,
    path: str,
    payload: Any,
    *,
    secret: str,
    delivery_id: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    """Serialize, sign, and post a JSON payload with standard ingest headers."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = ingest_headers(
        signature=sign_body(body, secret),
        delivery_id=delivery_id,
    )
    if extra_headers is not None:
        headers.update(extra_headers)
    return client.post(path, content=body, headers=headers)


def create_ingest_app(
    storage_root: str,
    *,
    secret: str | None = None,
    require_delivery_id: bool = False,
):
    """Create the application used by signed-ingest HTTP tests."""
    return create_app(
        make_settings(
            storage_root,
            secret=secret,
            require_delivery_id=require_delivery_id,
        )
    )


def assert_json_error(response: Any, *, status_code: int, code: str) -> None:
    """Assert the stable error status and JSON envelope returned by middleware."""
    assert response.status_code == status_code
    assert response.json() == {"detail": code, "code": code}


def make_body_limit_settings(
    storage_root: str,
    max_bytes: int,
    *,
    secret: str | None = None,
    rate_limit: dict[str, Any] | None = None,
) -> Settings:
    """Build settings for a body-limit scenario with optional rate-limit overrides."""
    hardening: dict[str, Any] = {"body_size_limit": {"max_bytes": max_bytes}}
    if rate_limit is not None:
        hardening["rate_limit"] = rate_limit
    return make_settings(
        storage_root,
        secret=secret,
        overrides={"hardening": hardening},
    )


def make_rate_limit_settings(
    storage_root: str,
    *,
    secret: str,
    rps: float = 0,
    burst: int = 2,
    body_max_bytes: int = 1024 * 1024,
) -> Settings:
    """Build settings for a two-request burst rate-limit scenario."""
    return make_settings(
        storage_root,
        secret=secret,
        overrides={
            "hardening": {
                "rate_limit": {"enabled": True, "rps": rps, "burst": burst},
                "body_size_limit": {"max_bytes": body_max_bytes},
            }
        },
    )
