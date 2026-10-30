"""Pin canonical JSON, source digest stability and the policy identifier."""

from __future__ import annotations

import copy
import hashlib
from typing import Any

from chronikwerk.preservation.digest import (
    SOURCE_ARTICLE_FIELDS,
    SOURCE_TICKET_FIELDS,
    canonical_json,
    policy_id,
    select_fields,
    sha256_tag,
    source_digest,
)

_TICKET: dict[str, Any] = {
    "number": "31042",
    "title": "Access",
    "group": "IT",
    "state": "closed",
    "priority": "2 normal",
    "owner": "alice",
    "customer": "bob",
    "organization": "Org",
    "archive_path": ["2026"],
    "archive_user_mode": "owner",
    "archive_user": "alice",
    "record_class": "pilot-test",
}
_ARTICLES: list[dict[str, Any]] = [
    {"id": 2, "type": "note", "body": "second", "created_at": "2026-01-02T00:00:00Z"},
    {"id": 10, "type": "email", "body": "tenth", "subject": "S", "message_id": "<m@x>"},
    {"id": 1, "type": "email", "body": "first", "from": "a@x", "to": "b@x", "cc": None},
]
_HASHES = {"source/articles/1.eml": "a" * 64, "attachments/1/5/a.txt": "b" * 64}


def _digest(**overrides: Any) -> str:
    """Compute the source digest of the sample inputs with optional overrides."""
    arguments: dict[str, Any] = {
        "ticket_fields": _TICKET,
        "articles": _ARTICLES,
        "file_hashes": _HASHES,
    }
    arguments.update(overrides)
    return source_digest(**arguments)


def test_canonical_json_sorts_keys_and_keeps_unicode() -> None:
    assert canonical_json({"b": 1, "a": "ü"}) == '{"a":"ü","b":1}'.encode()


def test_sha256_tag_prefixes_hex_digest() -> None:
    assert sha256_tag(b"abc") == "sha256:" + hashlib.sha256(b"abc").hexdigest()


def test_field_lists_match_the_contract() -> None:
    assert SOURCE_ARTICLE_FIELDS == (
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
    assert SOURCE_TICKET_FIELDS[0] == "number"
    assert SOURCE_TICKET_FIELDS[-1] == "record_class"


def test_select_fields_fills_missing_with_none() -> None:
    assert select_fields({"a": 1, "b": 2}, ("a", "c")) == {"a": 1, "c": None}


def test_source_digest_ignores_input_order() -> None:
    reordered = _digest(
        articles=list(reversed(_ARTICLES)),
        file_hashes=dict(reversed(list(_HASHES.items()))),
    )
    assert reordered == _digest()
    assert _digest().startswith("sha256:")


def test_source_digest_changes_with_body_ticket_and_file_hash() -> None:
    changed = copy.deepcopy(_ARTICLES)
    changed[0]["body"] = "edited"
    assert _digest(articles=changed) != _digest()
    assert _digest(ticket_fields={**_TICKET, "title": "Other"}) != _digest()
    assert _digest(file_hashes={**_HASHES, "source/articles/1.eml": "c" * 64}) != _digest()


def test_source_digest_ignores_tags_and_update_timestamps() -> None:
    noisy_ticket = {**_TICKET, "tags": ["pdf:signed"], "updated_at": "now", "article_count": 3}
    noisy_articles = [{**article, "updated_at": "later"} for article in _ARTICLES]
    assert _digest(ticket_fields=noisy_ticket, articles=noisy_articles) == _digest()


def test_policy_id_depends_on_rule_and_reading_copies() -> None:
    rule = {"retention": "P30D", "trigger": "capture"}
    base = policy_id(record_class_rule=rule, reading_copies=["archival", "accessible"])
    assert base.startswith("sha256:")
    assert base == policy_id(
        record_class_rule={"trigger": "capture", "retention": "P30D"},
        reading_copies=["archival", "accessible"],
    )
    assert base != policy_id(record_class_rule=rule, reading_copies=["archival"])
    assert base != policy_id(
        record_class_rule={**rule, "retention": "P1Y"}, reading_copies=["archival", "accessible"]
    )
