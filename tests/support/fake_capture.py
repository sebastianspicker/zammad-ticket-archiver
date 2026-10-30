"""Serve fixture Zammad bodies to source capture and its downstream tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chronikwerk.archiving.capture import CaptureOptions
from chronikwerk.zammad.dto import TagList
from chronikwerk.zammad.errors import ResponseTooLargeError

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "zammad"
TICKET = json.loads((_FIXTURES / "ticket_show_expand.json").read_text())
ARTICLES = json.loads((_FIXTURES / "ticket_articles_by_ticket.json").read_text())
EML = (_FIXTURES / "article_plain_101.eml").read_bytes()
ATTACHMENT = bytes(range(256)) * 117  # 29952 bytes, close to the declared "29935"
LATER = "2026-10-30T10:00:00.000Z"
CLOCK_VALUE = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
CAPTURE_OPTIONS = CaptureOptions(
    consistency_attempts=3,
    max_attachment_bytes=1024 * 1024,
    max_package_bytes=10 * 1024 * 1024,
    download_timeout_seconds=5.0,
    max_temp_bytes=20 * 1024 * 1024,
    max_metadata_bytes=1024 * 1024,
    record_class_field_name="record_class",
    known_record_classes=frozenset({"it-authorization", "general"}),
    archive_path_field_name="archive_path",
    archive_user_mode_field_name="archive_user_mode",
    archive_user_field_name="archive_user",
)


def fixture_ticket(**changes: Any) -> dict[str, Any]:
    """Return the fixture ticket with a known record class and ``changes`` applied."""
    return {**TICKET, "record_class": "it-authorization", **changes}


class FakeCaptureClient:
    """In-memory Zammad client serving fixture bodies; ticket versions advance per fetch.

    Besides the capture calls it keeps a tag set and records internal notes, so it can stand
    in for the gateway in pipeline tests. The ``fail_*`` switches make single calls fail.
    """

    def __init__(
        self,
        *,
        tickets: Callable[[int], dict[str, Any]] = lambda _call: fixture_ticket(),
        articles: list[dict[str, Any]] | None = None,
        emls: dict[int, bytes] | None = None,
        attachments: dict[int, bytes] | None = None,
        tags: Iterable[str] = (),
    ) -> None:
        """Serve ``tickets(call_index)`` for the n-th ticket fetch and fixed other bodies."""
        self._tickets = tickets
        self._articles = ARTICLES if articles is None else articles
        self._emls = {101: EML} if emls is None else emls
        self._attachments = {501: ATTACHMENT} if attachments is None else attachments
        self.ticket_calls = 0
        self.tags: set[str] = set(tags)
        self.tag_calls: list[tuple[str, str]] = []
        self.notes: list[tuple[str, str]] = []
        self.current_login = "chronikwerk"
        self.fail_ticket_with: BaseException | None = None
        self.fail_list_tags_with: Exception | None = None
        self.fail_adding_tag: str | None = None
        self.fail_notes = False

    async def list_tags(self, ticket_id: int) -> TagList:
        """Return the current tag set, or raise the configured failure."""
        if self.fail_list_tags_with is not None:
            raise self.fail_list_tags_with
        return TagList(sorted(self.tags))

    async def add_tag(self, ticket_id: int, tag: str) -> None:
        """Record and apply a tag addition; ``fail_adding_tag`` makes that tag fail."""
        self.tag_calls.append(("add", tag))
        if tag == self.fail_adding_tag:
            raise RuntimeError(f"Zammad refused tag {tag}")
        self.tags.add(tag)

    async def remove_tag(self, ticket_id: int, tag: str) -> None:
        """Record and apply a tag removal."""
        self.tag_calls.append(("remove", tag))
        self.tags.discard(tag)

    async def create_internal_article(self, ticket_id: int, subject: str, body: str) -> None:
        """Record an internal note, or fail like an unavailable Zammad."""
        if self.fail_notes:
            raise RuntimeError("note endpoint unavailable")
        self.notes.append((subject, body))

    async def get_current_user_login(self) -> str:
        """Return the login of the API user this client stands for."""
        return self.current_login

    async def get_ticket_raw(self, ticket_id: int, *, max_bytes: int) -> bytes:
        """Return the next ticket version as JSON bytes, or raise the configured failure."""
        if self.fail_ticket_with is not None:
            raise self.fail_ticket_with
        body = json.dumps(self._tickets(self.ticket_calls)).encode()
        self.ticket_calls += 1
        return self._bounded(body, max_bytes)

    async def list_articles_raw(self, ticket_id: int, *, max_bytes: int) -> bytes:
        """Return the article list as JSON bytes."""
        return self._bounded(json.dumps(self._articles).encode(), max_bytes)

    async def get_article_plain(self, article_id: int, *, max_bytes: int) -> bytes | None:
        """Return the stored EML, or None like the gateway does on 404."""
        eml = self._emls.get(article_id)
        return None if eml is None else self._bounded(eml, max_bytes)

    async def download_attachment(
        self,
        ticket_id: int,
        article_id: int,
        attachment_id: int,
        *,
        target: Path,
        max_bytes: int,
        timeout_seconds: float,
    ) -> int:
        """Write the attachment exclusively to ``target``; oversize leaves no file."""
        data = self._bounded(self._attachments[attachment_id], max_bytes)
        with target.open("xb") as handle:
            handle.write(data)
        return len(data)

    @staticmethod
    def _bounded(data: bytes, max_bytes: int) -> bytes:
        """Raise the gateway's ResponseTooLargeError when ``data`` exceeds ``max_bytes``."""
        if len(data) > max_bytes:
            raise ResponseTooLargeError("too large", limit=max_bytes)
        return data
