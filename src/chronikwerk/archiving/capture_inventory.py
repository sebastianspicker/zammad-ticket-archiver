"""Parse verbatim capture bodies and derive inventories, record class and mail-auth facts."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from email import policy
from email.parser import BytesHeaderParser
from typing import Any

from pydantic import ValidationError

from chronikwerk.archiving.capture_errors import RecordClassMissingError
from chronikwerk.zammad.dto import Article, Ticket
from chronikwerk.zammad.errors import ClientError

__all__ = [
    "inventory",
    "mail_auth_from_eml",
    "parse_articles_body",
    "parse_ticket_body",
    "read_record_class",
]


def _load_json(data: bytes, *, what: str) -> Any:
    try:
        return json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClientError(f"Zammad {what} response is not valid JSON: {exc!s}") from exc


def parse_ticket_body(data: bytes, *, ticket_id: int) -> tuple[dict[str, Any], Ticket]:
    """Return the raw ticket object and its model; the body must describe ``ticket_id``."""
    raw = _load_json(data, what="ticket")
    if not isinstance(raw, dict):
        raise ClientError("Zammad ticket response is not a JSON object")
    try:
        ticket = Ticket.model_validate(raw)
    except ValidationError as exc:
        raise ClientError(f"Zammad ticket response format unexpected: {exc!s}") from exc
    if ticket.id != ticket_id:
        raise ClientError(f"Zammad returned ticket {ticket.id} when asked for {ticket_id}")
    return raw, ticket


def parse_articles_body(data: bytes) -> tuple[list[dict[str, Any]], tuple[Article, ...]]:
    """Return the raw article objects and their models; every attachment needs an id."""
    raw = _load_json(data, what="article list")
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ClientError("Zammad article list response is not a JSON array of objects")
    try:
        articles = tuple(Article.model_validate(item) for item in raw)
    except ValidationError as exc:
        raise ClientError(f"Zammad article response format unexpected: {exc!s}") from exc
    for article in articles:
        if any(attachment.id is None for attachment in article.attachments or ()):
            raise ClientError(f"Zammad article {article.id} lists an attachment without id")
    return raw, articles


def inventory(
    ticket_raw: Mapping[str, Any],
    articles_raw: Sequence[Mapping[str, Any]],
    articles: Sequence[Article],
) -> dict[str, Any]:
    """Return the change-detection inventory: ticket, article and attachment versions."""
    article_versions = sorted(
        [article.id, raw.get("updated_at")]
        for raw, article in zip(articles_raw, articles, strict=True)
    )
    attachment_sizes = sorted(
        [attachment.id or 0, attachment.size]
        for article in articles
        for attachment in article.attachments or ()
    )
    return {
        "ticket_updated_at": ticket_raw.get("updated_at"),
        "articles": article_versions,
        "attachments": attachment_sizes,
    }


def read_record_class(ticket: Ticket, *, field_name: str, known: Collection[str]) -> str:
    """Return the ticket's record class, which must be one the policy knows."""
    value = ticket.attribute(field_name)
    record_class = value.strip() if isinstance(value, str) else ""
    if not record_class:
        raise RecordClassMissingError(f"ticket {ticket.id} has no {field_name} value")
    if record_class not in known:
        raise RecordClassMissingError(
            f"ticket {ticket.id} has record class {record_class!r}, which the policy does not know"
        )
    return record_class


def _dkim_domain(signature: str) -> str | None:
    for tag in signature.split(";"):
        name, sep, value = tag.partition("=")
        if sep and name.strip() == "d":
            return "".join(value.split())
    return None


MAIL_AUTH_ORDER = "as-in-message, topmost first"
MAIL_AUTH_PROVENANCE = "copied from the original message headers, not validated"


def mail_auth_from_eml(eml: bytes) -> dict[str, Any]:
    """Copy what the receiving MTAs reported; nothing here is validated.

    Header values keep their order in the message (topmost, that is most recently added,
    first) and the record says so; ``verified`` is always false because neither the
    ``Authentication-Results`` claims nor the DKIM signatures are checked.
    """
    headers = BytesHeaderParser(policy=policy.default).parsebytes(eml)
    return {
        "provenance": MAIL_AUTH_PROVENANCE,
        "order": MAIL_AUTH_ORDER,
        "verified": False,
        "authentication_results": [
            str(value) for value in headers.get_all("Authentication-Results") or ()
        ],
        "received_spf": [str(value) for value in headers.get_all("Received-SPF") or ()],
        "dkim_signature_domains": [
            _dkim_domain(str(value)) for value in headers.get_all("DKIM-Signature") or ()
        ],
    }
