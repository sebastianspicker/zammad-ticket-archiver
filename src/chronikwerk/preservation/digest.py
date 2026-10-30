"""Canonical JSON, SHA-256 tags, the source digest and the policy identifier."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from chronikwerk.preservation.manifest import PACKAGE_FORMAT

__all__ = [
    "SOURCE_ARTICLE_FIELDS",
    "SOURCE_TICKET_FIELDS",
    "canonical_json",
    "policy_id",
    "select_fields",
    "sha256_tag",
    "source_digest",
]

SOURCE_TICKET_FIELDS = (
    "number",
    "title",
    "group",
    "state",
    "priority",
    "owner",
    "customer",
    "organization",
    "archive_path",
    "archive_user_mode",
    "archive_user",
    "record_class",
)
SOURCE_ARTICLE_FIELDS = (
    "id",
    "type",
    "sender",
    "internal",
    "from",
    "to",
    "cc",
    "subject",
    "body",
    "created_at",
    "message_id",
)


def canonical_json(obj: Any) -> bytes:
    """Serialize ``obj`` as key-sorted, whitespace-free UTF-8 JSON."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sha256_tag(data: bytes) -> str:
    """Return ``sha256:<hex>`` for ``data``."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def select_fields(mapping: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    """Pick ``fields`` from ``mapping``; missing keys become ``None``."""
    return {field: mapping.get(field) for field in fields}


def _article_order(article: Mapping[str, Any]) -> tuple[int, int, str]:
    """Order articles by numeric id, with non-numeric ids after them."""
    article_id = article["id"]
    if isinstance(article_id, int) and not isinstance(article_id, bool):
        return (0, article_id, "")
    return (1, 0, str(article_id))


def source_digest(
    *,
    ticket_fields: Mapping[str, Any],
    articles: Sequence[Mapping[str, Any]],
    file_hashes: Mapping[str, str],
) -> str:
    """Digest the content-bearing source fields, ignoring tags and update timestamps."""
    selected_articles = sorted(
        (select_fields(article, SOURCE_ARTICLE_FIELDS) for article in articles),
        key=_article_order,
    )
    payload = {
        "ticket": select_fields(ticket_fields, SOURCE_TICKET_FIELDS),
        "articles": selected_articles,
        "files": [[path, file_hashes[path]] for path in sorted(file_hashes)],
    }
    return sha256_tag(canonical_json(payload))


def policy_id(*, record_class_rule: Mapping[str, Any], reading_copies: Sequence[str]) -> str:
    """Digest the rule, package format and reading copies that shaped a capture."""
    payload = {
        "format": PACKAGE_FORMAT,
        "record_class_rule": record_class_rule,
        "reading_copies": list(reading_copies),
    }
    return sha256_tag(canonical_json(payload))
