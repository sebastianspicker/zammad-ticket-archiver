"""Shared constants for the web layer."""

import structlog
from starlette.types import Scope

log = structlog.get_logger(__name__)

DELIVERY_ID_HEADER = "X-Zammad-Delivery"
MAX_DELIVERY_ID_CHARS = 128
INGEST_PROTECTED_PATHS: frozenset[str] = frozenset({"/ingest", "/ingest/batch"})


def route_path(scope: Scope) -> str:
    """Return the application-relative path using Starlette routing semantics."""
    path = str(scope.get("path") or "")
    root_path = str(scope.get("root_path") or "")
    if not root_path or not path.startswith(root_path):
        return path
    if path == root_path:
        return ""
    if path[len(root_path)] == "/":
        return path[len(root_path) :]
    return path


def normalized_delivery_id(value: str | None) -> str | None:
    """Strip a delivery identifier header value and map blank or invalid input to ``None``.

    A valid identifier has at most ``MAX_DELIVERY_ID_CHARS`` printable ASCII characters;
    anything else is logged and treated as absent, so deduplication never keys on it.
    """
    text = (value or "").strip()
    if not text:
        return None
    if len(text) > MAX_DELIVERY_ID_CHARS or not all(" " <= char <= "~" for char in text):
        log.warning("ingest.delivery_id_ignored", length=len(text))
        return None
    return text
