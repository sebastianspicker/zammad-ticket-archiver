"""Shared constants for the web layer."""

DELIVERY_ID_HEADER = "X-Zammad-Delivery"
INGEST_PROTECTED_PATHS: frozenset[str] = frozenset({"/ingest", "/ingest/batch"})
