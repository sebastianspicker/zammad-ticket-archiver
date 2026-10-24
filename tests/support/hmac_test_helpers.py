"""Shared HMAC construction helpers for focused webhook tests."""

from __future__ import annotations

import hmac


def sign_body(body: bytes, secret: str, *, algorithm: str = "sha256") -> str:
    """Create the body-only signature accepted by legacy webhook mode."""
    digest = hmac.new(secret.encode("utf-8"), body, algorithm).hexdigest()
    return f"{algorithm}={digest}"


def sign_zammad_native(body: bytes, secret: str) -> str:
    """Create the header Zammad itself sends: ``sha1=`` + HMAC-SHA1 over the raw body."""
    return sign_body(body, secret, algorithm="sha1")


def sign_strict(delivery_id: str, body: bytes, secret: str, *, algorithm: str = "sha256") -> str:
    """Create the delivery-bound signature required by strict webhook mode."""
    delivery_id_bytes = delivery_id.strip().encode("utf-8")
    canonical = (
        b"zammad-webhook-v1\0"
        + len(delivery_id_bytes).to_bytes(8, byteorder="big")
        + delivery_id_bytes
        + body
    )
    digest = hmac.new(secret.encode("utf-8"), canonical, algorithm).hexdigest()
    return f"{algorithm}={digest}"
