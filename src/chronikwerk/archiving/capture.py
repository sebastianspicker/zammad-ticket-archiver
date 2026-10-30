"""Collect one Zammad ticket completely and coherently into a package build directory."""

# Contract: preservation brief (ADRs 0011-0013) section 5.3, steps 2-10.

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from chronikwerk.archiving.capture_errors import (
    AttachmentTooLargeError,
    InconsistentSourceError,
    TempSpaceExceededError,
)
from chronikwerk.archiving.capture_inventory import (
    inventory,
    mail_auth_from_eml,
    parse_articles_body,
    parse_ticket_body,
    read_record_class,
)
from chronikwerk.preservation.digest import source_digest
from chronikwerk.preservation.manifest import (
    Coverage,
    CoverageCount,
    FileRole,
    ManifestFile,
    OriginalEmailCoverage,
)
from chronikwerk.preservation.package import PackageBuilder, sanitize_filename
from chronikwerk.timestamps import now_utc
from chronikwerk.zammad.dto import Article, AttachmentMeta, Ticket
from chronikwerk.zammad.errors import ResponseTooLargeError

__all__ = ["CaptureClient", "CaptureOptions", "CapturedSource", "capture_ticket"]

_JSON = "application/json"
_EML = "message/rfc822"
_OCTET_STREAM = "application/octet-stream"
_EMAIL_TYPE = "email"
# RFC 6838 section 4.2: type "/" subtype, each a restricted-name of at most 127 characters.
_RESTRICTED_NAME = r"[a-z0-9][a-z0-9!#$&^_.+-]{0,126}"
_MEDIA_TYPE_RE = re.compile(rf"{_RESTRICTED_NAME}/{_RESTRICTED_NAME}")


@dataclass(frozen=True, slots=True)
class CaptureOptions:
    """Limits and ticket field names that shape one source capture."""

    consistency_attempts: int
    max_attachment_bytes: int
    max_package_bytes: int
    download_timeout_seconds: float
    max_temp_bytes: int
    max_metadata_bytes: int
    record_class_field_name: str
    known_record_classes: frozenset[str]
    archive_path_field_name: str
    archive_user_mode_field_name: str
    archive_user_field_name: str

    def __post_init__(self) -> None:
        """Reject limits that would make every capture fail or never stop."""
        if self.consistency_attempts < 1:
            raise ValueError("consistency_attempts must be at least 1")
        for name in (
            "max_attachment_bytes",
            "max_package_bytes",
            "max_temp_bytes",
            "max_metadata_bytes",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.download_timeout_seconds <= 0:
            raise ValueError("download_timeout_seconds must be positive")


class CaptureClient(Protocol):
    """The Zammad gateway calls that source capture uses."""

    async def get_ticket_raw(self, ticket_id: int, *, max_bytes: int) -> bytes:
        """Return the verbatim expanded ticket body."""
        ...

    async def list_articles_raw(self, ticket_id: int, *, max_bytes: int) -> bytes:
        """Return the verbatim by-ticket article list body."""
        ...

    async def get_article_plain(self, article_id: int, *, max_bytes: int) -> bytes | None:
        """Return an email article's original message, or ``None`` when Zammad has none."""
        ...

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
        """Stream one attachment into the new file ``target`` and return its size."""
        ...


@dataclass(frozen=True, slots=True)
class CapturedSource:
    """A consistent capture: package build directory, source facts and capture metadata."""

    build_dir: Path
    ticket: Ticket
    ticket_raw: Mapping[str, Any]
    articles: tuple[Article, ...]
    record_class: str
    source_digest: str
    coverage: Coverage
    mail_auth: Mapping[int, Mapping[str, Any]]
    inventory_before: Mapping[str, Any]
    inventory_after: Mapping[str, Any]
    attempts: int
    collection_started: datetime
    collection_finished: datetime
    builder: PackageBuilder


class _TempBudget:
    """Bytes written below the capture work directory, across all attempts."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._used = 0

    @property
    def remaining(self) -> int:
        return self._limit - self._used

    def reserve(self, size: int) -> None:
        if self._used + size > self._limit:
            raise TempSpaceExceededError(
                f"capture would write more than {self._limit} temporary bytes"
            )
        self._used += size


@dataclass(slots=True)
class _Attempt:
    """Mutable state of one collection attempt."""

    ticket_id: int
    builder: PackageBuilder
    build_dir: Path
    ticket_raw: dict[str, Any]
    ticket: Ticket
    articles_raw: list[dict[str, Any]]
    articles: tuple[Article, ...]
    inventory_before: dict[str, Any]
    record_class: str
    file_hashes: dict[str, str] = field(default_factory=dict)
    mail_auth: dict[int, dict[str, Any]] = field(default_factory=dict)
    unavailable: list[int] = field(default_factory=list)
    attachments_included: int = 0


async def capture_ticket(
    client: CaptureClient,
    *,
    ticket_id: int,
    work_dir: Path,
    options: CaptureOptions,
    clock: Callable[[], datetime] = now_utc,
) -> CapturedSource:
    """
    Collect ``ticket_id`` into ``work_dir/attempt-<n>/`` until two inventories agree.

    ``work_dir`` is an existing, empty, caller-owned temporary directory. Earlier attempt
    directories stay in place for the caller's cleanup and count against ``max_temp_bytes``.
    """
    started = clock()
    budget = _TempBudget(options.max_temp_bytes)
    for attempt_number in range(1, options.consistency_attempts + 1):
        attempt_dir = work_dir / f"attempt-{attempt_number}"
        attempt = await _collect(client, ticket_id, attempt_dir, options, budget)
        after = await _fetch_inventory(client, ticket_id, options)
        if after == attempt.inventory_before:
            return _result(attempt, after, options, attempt_number, started, clock())
    raise InconsistentSourceError(
        f"ticket {ticket_id} changed during each of {options.consistency_attempts} capture attempts"
    )


async def _fetch_bodies(
    client: CaptureClient, ticket_id: int, options: CaptureOptions
) -> tuple[bytes, bytes]:
    ticket_body = await client.get_ticket_raw(ticket_id, max_bytes=options.max_metadata_bytes)
    articles_body = await client.list_articles_raw(ticket_id, max_bytes=options.max_metadata_bytes)
    return ticket_body, articles_body


async def _fetch_inventory(
    client: CaptureClient, ticket_id: int, options: CaptureOptions
) -> dict[str, Any]:
    ticket_body, articles_body = await _fetch_bodies(client, ticket_id, options)
    ticket_raw, _ = parse_ticket_body(ticket_body, ticket_id=ticket_id)
    articles_raw, articles = parse_articles_body(articles_body)
    return inventory(ticket_raw, articles_raw, articles)


async def _collect(
    client: CaptureClient,
    ticket_id: int,
    attempt_dir: Path,
    options: CaptureOptions,
    budget: _TempBudget,
) -> _Attempt:
    attempt_dir.mkdir()
    builder = PackageBuilder(attempt_dir, max_package_bytes=options.max_package_bytes)
    ticket_body, articles_body = await _fetch_bodies(client, ticket_id, options)
    ticket_raw, ticket = parse_ticket_body(ticket_body, ticket_id=ticket_id)
    articles_raw, articles = parse_articles_body(articles_body)
    attempt = _Attempt(
        ticket_id=ticket_id,
        builder=builder,
        build_dir=attempt_dir,
        ticket_raw=ticket_raw,
        ticket=ticket,
        articles_raw=articles_raw,
        articles=articles,
        inventory_before=inventory(ticket_raw, articles_raw, articles),
        record_class=read_record_class(
            ticket,
            field_name=options.record_class_field_name,
            known=options.known_record_classes,
        ),
    )
    _add_bytes(attempt, budget, "source/ticket.json", ticket_body, FileRole.SOURCE_TICKET, _JSON)
    _add_bytes(
        attempt, budget, "source/articles.json", articles_body, FileRole.SOURCE_ARTICLES, _JSON
    )
    for article in articles:
        if article.type == _EMAIL_TYPE:
            await _capture_email(client, attempt, article, options, budget)
        for attachment in article.attachments or ():
            await _capture_attachment(client, attempt, article, attachment, options, budget)
    return attempt


def _add_bytes(
    attempt: _Attempt,
    budget: _TempBudget,
    path: str,
    data: bytes,
    role: FileRole,
    media_type: str,
) -> ManifestFile:
    budget.reserve(len(data))
    return attempt.builder.add_bytes(path, data, role=role, media_type=media_type)


async def _capture_email(
    client: CaptureClient,
    attempt: _Attempt,
    article: Article,
    options: CaptureOptions,
    budget: _TempBudget,
) -> None:
    eml = await client.get_article_plain(article.id, max_bytes=options.max_metadata_bytes)
    if eml is None:
        attempt.unavailable.append(article.id)
        return
    path = f"source/articles/{article.id}.eml"
    entry = _add_bytes(attempt, budget, path, eml, FileRole.ORIGINAL_EMAIL, _EML)
    attempt.file_hashes[path] = entry.sha256
    attempt.mail_auth[article.id] = mail_auth_from_eml(eml)


async def _download(
    client: CaptureClient,
    attempt: _Attempt,
    ids: tuple[int, int],
    target: Path,
    options: CaptureOptions,
    budget: _TempBudget,
) -> None:
    """Stream one attachment to ``target``, bounded by the attachment and temp limits."""
    article_id, attachment_id = ids
    limit = min(options.max_attachment_bytes, budget.remaining)
    if limit < 1:
        raise TempSpaceExceededError("no temporary space left for attachment downloads")
    try:
        written = await client.download_attachment(
            attempt.ticket_id,
            article_id,
            attachment_id,
            target=target,
            max_bytes=limit,
            timeout_seconds=options.download_timeout_seconds,
        )
    except ResponseTooLargeError as exc:
        if limit < options.max_attachment_bytes:
            raise TempSpaceExceededError(
                f"attachment {attachment_id} exceeds the remaining temporary space"
            ) from exc
        raise AttachmentTooLargeError(
            f"attachment {attachment_id} of article {article_id} exceeds "
            f"{options.max_attachment_bytes} bytes"
        ) from exc
    budget.reserve(written)


async def _capture_attachment(
    client: CaptureClient,
    attempt: _Attempt,
    article: Article,
    attachment: AttachmentMeta,
    options: CaptureOptions,
    budget: _TempBudget,
) -> None:
    attachment_id = attachment.id or 0  # parse_articles_body rejects attachments without id
    temp = attempt.build_dir / f".partial-{article.id}-{attachment_id}"
    await _download(client, attempt, (article.id, attachment_id), temp, options, budget)
    filename = sanitize_filename(attachment.filename or "")
    path = f"attachments/{article.id}/{attachment_id}/{filename}"
    try:
        entry = attempt.builder.add_file(
            path,
            temp,
            role=FileRole.ATTACHMENT,
            media_type=_attachment_media_type(attachment),
            move=True,
        )
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    attempt.file_hashes[path] = entry.sha256
    attempt.attachments_included += 1


def _attachment_media_type(attachment: AttachmentMeta) -> str:
    """Return Zammad's media type reduced to a lowercase RFC 6838 ``type/subtype``.

    Parameters are dropped; a value that is not a well-formed media type is recorded as
    ``application/octet-stream``.
    """
    preferences = attachment.preferences or {}
    for key in ("Content-Type", "Mime-Type"):
        value = preferences.get(key)
        if isinstance(value, str) and value.strip():
            media_type = value.split(";", 1)[0].strip().lower()
            return media_type if _MEDIA_TYPE_RE.fullmatch(media_type) else _OCTET_STREAM
    return _OCTET_STREAM


def _coverage(attempt: _Attempt) -> Coverage:
    """
    Count what the capture holds.

    ``complete`` is true when every article and every attachment is included and every
    email-type article either has its original message or is listed as unavailable (Zammad
    holds none). An unavailable original does not make the capture incomplete.
    """
    article_total = len(attempt.articles)
    attachment_total = sum(len(article.attachments or ()) for article in attempt.articles)
    expected = sum(1 for article in attempt.articles if article.type == _EMAIL_TYPE)
    unavailable = tuple(sorted(attempt.unavailable))
    emails = OriginalEmailCoverage(expected, expected - len(unavailable), unavailable)
    articles = CoverageCount(article_total, article_total)
    attachments = CoverageCount(attachment_total, attempt.attachments_included)
    complete = (
        articles.included == articles.total
        and attachments.included == attachments.total
        and emails.included + len(emails.unavailable) == emails.expected
    )
    return Coverage(articles, attachments, emails, complete)


def _digest(attempt: _Attempt, options: CaptureOptions) -> str:
    raw = attempt.ticket_raw
    ticket_fields = {
        **raw,
        "archive_path": raw.get(options.archive_path_field_name),
        "archive_user_mode": raw.get(options.archive_user_mode_field_name),
        "archive_user": raw.get(options.archive_user_field_name),
        "record_class": attempt.record_class,
    }
    return source_digest(
        ticket_fields=ticket_fields,
        articles=attempt.articles_raw,
        file_hashes=attempt.file_hashes,
    )


def _result(
    attempt: _Attempt,
    inventory_after: dict[str, Any],
    options: CaptureOptions,
    attempts: int,
    started: datetime,
    finished: datetime,
) -> CapturedSource:
    return CapturedSource(
        build_dir=attempt.build_dir,
        ticket=attempt.ticket,
        ticket_raw=attempt.ticket_raw,
        articles=attempt.articles,
        record_class=attempt.record_class,
        source_digest=_digest(attempt, options),
        coverage=_coverage(attempt),
        mail_auth=attempt.mail_auth,
        inventory_before=attempt.inventory_before,
        inventory_after=inventory_after,
        attempts=attempts,
        collection_started=started,
        collection_finished=finished,
        builder=attempt.builder,
    )
