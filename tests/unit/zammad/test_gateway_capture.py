"""Pin the wire contract and bounds of the capture-side Zammad gateway calls."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from chronikwerk.configuration.zammad import ZammadConnection
from chronikwerk.zammad.dto import Article, AttachmentMeta
from chronikwerk.zammad.errors import ClientError, ResponseTooLargeError, ServerError
from chronikwerk.zammad.gateway import AsyncZammadClient, trigger_tag_query

_ORIGIN = "https://zammad.example"
_AUTH = "Token token=token-value"
_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "zammad"
_ATTACHMENT_PATH = f"{_ORIGIN}/api/v1/ticket_attachment/42/101/501"


def _run(call: Any) -> Any:
    """Run one client coroutine factory on a fresh client and close the client."""

    async def scenario() -> Any:
        connection = ZammadConnection(
            origin=_ORIGIN, api_token=SecretStr("token-value"), allow_private_origin=True
        )
        async with AsyncZammadClient(connection=connection) as client:
            return await call(client)

    return asyncio.run(scenario())


def _download(target: Path, *, max_bytes: int = 1024, timeout_seconds: float = 5.0) -> Any:
    """Download attachment 501 of article 101 on ticket 42 into ``target``."""
    return _run(
        lambda c: c.download_attachment(
            42,
            101,
            501,
            target=target,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
        )
    )


def test_get_ticket_raw_returns_the_verbatim_expanded_body() -> None:
    """get_ticket_raw sends GET /api/v1/tickets/{id}?expand=true and keeps the exact bytes."""
    raw = (_FIXTURES / "ticket_show_expand.json").read_bytes()
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{_ORIGIN}/api/v1/tickets/42").respond(200, content=raw)
        body = _run(lambda c: c.get_ticket_raw(42, max_bytes=65536))
    request = route.calls.last.request
    assert body == raw
    assert request.method == "GET"
    assert request.url.path == "/api/v1/tickets/42"
    assert dict(request.url.params) == {"expand": "true"}
    assert request.headers["authorization"] == _AUTH


def test_list_articles_raw_returns_the_verbatim_body() -> None:
    """list_articles_raw sends GET /api/v1/ticket_articles/by_ticket/{id} without params."""
    raw = (_FIXTURES / "ticket_articles_by_ticket.json").read_bytes()
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{_ORIGIN}/api/v1/ticket_articles/by_ticket/42").respond(200, content=raw)
        body = _run(lambda c: c.list_articles_raw(42, max_bytes=65536))
    request = route.calls.last.request
    assert body == raw
    assert request.method == "GET"
    assert request.url.path == "/api/v1/ticket_articles/by_ticket/42"
    assert request.url.query == b""
    assert request.headers["authorization"] == _AUTH


def test_raw_ticket_and_article_bodies_enforce_the_byte_limit() -> None:
    """Both raw metadata calls raise ResponseTooLargeError above max_bytes."""
    with respx.mock() as mock:
        mock.get(f"{_ORIGIN}/api/v1/tickets/42").respond(200, content=b"{}" * 50)
        mock.get(f"{_ORIGIN}/api/v1/ticket_articles/by_ticket/42").respond(200, content=b"[]" * 50)
        with pytest.raises(ResponseTooLargeError):
            _run(lambda c: c.get_ticket_raw(42, max_bytes=10))
        with pytest.raises(ResponseTooLargeError):
            _run(lambda c: c.list_articles_raw(42, max_bytes=10))


def test_get_article_plain_returns_the_raw_email_bytes() -> None:
    """get_article_plain issues GET /api/v1/ticket_article_plain/{id} and returns the bytes."""
    raw = (_FIXTURES / "article_plain_101.eml").read_bytes()
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{_ORIGIN}/api/v1/ticket_article_plain/101").respond(200, content=raw)
        body = _run(lambda c: c.get_article_plain(101, max_bytes=65536))
    request = route.calls.last.request
    assert body == raw
    assert b"<20261020075841.1@example.com>" in raw
    assert request.method == "GET"
    assert request.url.query == b""
    assert request.headers["authorization"] == _AUTH
    assert request.headers["accept-encoding"] == "identity"


def test_get_article_plain_returns_none_on_404() -> None:
    """A missing plain message is None, not an error, and is not retried."""
    with respx.mock() as mock:
        route = mock.get(f"{_ORIGIN}/api/v1/ticket_article_plain/7").respond(404)
        assert _run(lambda c: c.get_article_plain(7, max_bytes=1024)) is None
    assert route.call_count == 1


def test_get_article_plain_enforces_the_byte_limit() -> None:
    """A raw message above max_bytes raises ResponseTooLargeError carrying the limit."""
    with respx.mock() as mock:
        mock.get(f"{_ORIGIN}/api/v1/ticket_article_plain/7").respond(200, content=b"x" * 50)
        with pytest.raises(ResponseTooLargeError) as caught:
            _run(lambda c: c.get_article_plain(7, max_bytes=10))
    assert caught.value.limit == 10
    assert isinstance(caught.value, ClientError)


def test_download_attachment_streams_exact_bytes_to_the_target(tmp_path: Path) -> None:
    """The attachment route is called once and its bytes land in the new target file."""
    payload = bytes(range(256)) * 100
    target = tmp_path / "attachment.bin"
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(_ATTACHMENT_PATH).respond(200, content=payload)
        written = _download(target, max_bytes=len(payload))
    request = route.calls.last.request
    assert written == len(payload)
    assert target.read_bytes() == payload
    assert request.method == "GET"
    assert request.url.query == b""
    assert request.headers["authorization"] == _AUTH
    assert request.headers["accept-encoding"] == "identity"


def test_download_attachment_over_limit_raises_and_leaves_no_file(tmp_path: Path) -> None:
    """An oversize body stops the stream, raises, and removes the partial target."""
    target = tmp_path / "attachment.bin"
    with respx.mock() as mock:
        mock.get(_ATTACHMENT_PATH).respond(200, content=b"x" * 500_000)
        with pytest.raises(ResponseTooLargeError) as caught:
            _download(target, max_bytes=100_000)
    assert caught.value.limit == 100_000
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_download_attachment_declared_length_over_limit_creates_no_file(tmp_path: Path) -> None:
    """A Content-Length above the limit is rejected before the target is created."""
    target = tmp_path / "attachment.bin"
    with respx.mock() as mock:
        mock.get(_ATTACHMENT_PATH).respond(
            200, content=b"x" * 20, headers={"Content-Length": "9999"}
        )
        with pytest.raises(ResponseTooLargeError):
            _download(target, max_bytes=100)
    assert not target.exists()


def test_download_attachment_enforces_limit_when_content_length_lies(tmp_path: Path) -> None:
    """A body larger than its declared Content-Length is still bounded by max_bytes."""
    target = tmp_path / "attachment.bin"
    with respx.mock() as mock:
        mock.get(_ATTACHMENT_PATH).respond(
            200, content=b"x" * 5000, headers={"Content-Length": "5"}
        )
        with pytest.raises(ResponseTooLargeError):
            _download(target, max_bytes=1000)
    assert not target.exists()


def test_download_attachment_rejects_compressed_responses(tmp_path: Path) -> None:
    """A Content-Encoding other than identity is refused before any file is created."""
    target = tmp_path / "attachment.bin"
    with respx.mock() as mock:
        mock.get(_ATTACHMENT_PATH).respond(200, content=b"x", headers={"Content-Encoding": "gzip"})
        with pytest.raises(ClientError, match="compressed"):
            _download(target)
    assert not target.exists()


def test_download_attachment_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    """O_EXCL creation fails on an existing target and leaves it untouched."""
    target = tmp_path / "attachment.bin"
    target.write_bytes(b"keep")
    with respx.mock() as mock:
        mock.get(_ATTACHMENT_PATH).respond(200, content=b"new")
        with pytest.raises(FileExistsError):
            _download(target)
    assert target.read_bytes() == b"keep"


def test_download_attachment_timeout_is_transient_and_removes_the_partial_file(
    tmp_path: Path,
) -> None:
    """A stalled stream hits the total wall-clock timeout as a transient ServerError."""

    async def slow() -> AsyncIterator[bytes]:
        yield b"partial"
        await asyncio.sleep(30)
        yield b"never"

    target = tmp_path / "attachment.bin"
    with respx.mock() as mock:
        mock.get(_ATTACHMENT_PATH).mock(return_value=httpx.Response(200, content=slow()))
        with pytest.raises(ServerError, match="timed out"):
            _download(target, timeout_seconds=0.3)
    assert not target.exists()


def test_download_attachment_retries_after_a_mid_body_read_error(tmp_path: Path) -> None:
    """A connection reset mid-stream removes the partial file; the retry writes the full body."""

    async def broken() -> AsyncIterator[bytes]:
        yield b"partial"
        raise httpx.ReadError("connection reset mid-body")

    payload = b"complete attachment body"
    target = tmp_path / "attachment.bin"
    with respx.mock() as mock:
        route = mock.get(_ATTACHMENT_PATH).mock(
            side_effect=[
                httpx.Response(200, content=broken()),
                httpx.Response(200, content=payload),
            ]
        )
        written = _download(target, max_bytes=1024)
    assert route.call_count == 2
    assert written == len(payload)
    assert target.read_bytes() == payload
    assert list(tmp_path.iterdir()) == [target]


def test_download_attachment_refuses_a_symlinked_target(tmp_path: Path) -> None:
    """A planted (dangling) symlink at the target is never followed or replaced."""
    target = tmp_path / "attachment.bin"
    elsewhere = tmp_path / "elsewhere.bin"
    target.symlink_to(elsewhere)
    with respx.mock() as mock:
        mock.get(_ATTACHMENT_PATH).respond(200, content=b"new")
        with pytest.raises(OSError):
            _download(target)
    assert target.is_symlink()
    assert not elsewhere.exists()


def test_download_attachment_failure_keeps_a_file_that_replaced_the_target(
    tmp_path: Path,
) -> None:
    """Cleanup unlinks only the file this download created, not one swapped in meanwhile."""
    target = tmp_path / "attachment.bin"

    async def swap_then_overflow() -> AsyncIterator[bytes]:
        yield b"x" * 10
        target.unlink()
        target.write_bytes(b"someone else's file")
        yield b"x" * 5000

    with respx.mock() as mock:
        mock.get(_ATTACHMENT_PATH).mock(
            return_value=httpx.Response(200, content=swap_then_overflow())
        )
        with pytest.raises(ResponseTooLargeError):
            _download(target, max_bytes=1000)
    assert target.read_bytes() == b"someone else's file"


def test_request_bytes_total_deadline_is_a_transient_server_error() -> None:
    """request_bytes with timeout_seconds bounds the whole request, retries included."""

    async def slow() -> AsyncIterator[bytes]:
        yield b"partial"
        await asyncio.sleep(30)
        yield b"never"

    with respx.mock() as mock:
        mock.get(f"{_ORIGIN}/api/v1/ticket_article_plain/7").mock(
            return_value=httpx.Response(200, content=slow())
        )
        with pytest.raises(ServerError, match="timed out"):
            _run(
                lambda c: c._transport.request_bytes(
                    "GET", "api/v1/ticket_article_plain/7", max_bytes=1024, timeout_seconds=0.3
                )
            )


def test_download_attachment_maps_http_errors_without_creating_a_file(tmp_path: Path) -> None:
    """A 404 raises the usual NotFoundError and creates no target file."""
    target = tmp_path / "attachment.bin"
    with respx.mock() as mock:
        mock.get(_ATTACHMENT_PATH).respond(404)
        with pytest.raises(ClientError, match="status=404"):
            _download(target)
    assert not target.exists()


@pytest.mark.parametrize("fixture", ["tickets_search.json", "tickets_search_list.json"])
def test_search_ticket_ids_accepts_both_response_shapes(fixture: str) -> None:
    """search_ticket_ids sends query/limit/page and reads IDs from either response shape."""
    body = json.loads((_FIXTURES / fixture).read_text())
    query = trigger_tag_query("pdf:sign")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{_ORIGIN}/api/v1/tickets/search").respond(200, json=body)
        ids = _run(lambda c: c.search_ticket_ids(query, limit=50, page=2))
    request = route.calls.last.request
    assert ids == [42, 43]
    assert request.method == "GET"
    assert request.url.path == "/api/v1/tickets/search"
    assert dict(request.url.params) == {"query": 'tags:"pdf:sign"', "limit": "50", "page": "2"}
    assert request.headers["authorization"] == _AUTH


def test_search_ticket_ids_defaults_and_malformed_payload() -> None:
    """Defaults are limit=100 page=1, and an unexpected payload is a ClientError."""
    with respx.mock() as mock:
        route = mock.get(f"{_ORIGIN}/api/v1/tickets/search").respond(200, json={"tickets": []})
        assert _run(lambda c: c.search_ticket_ids("q")) == []
        assert dict(route.calls.last.request.url.params) == {
            "query": "q",
            "limit": "100",
            "page": "1",
        }
        mock.get(f"{_ORIGIN}/api/v1/tickets/search").respond(200, json={"tickets": ["x"]})
        with pytest.raises(ClientError, match="non-integer"):
            _run(lambda c: c.search_ticket_ids("q"))


def test_trigger_tag_query_escapes_quotes_and_backslashes() -> None:
    """Quotes and backslashes in a tag cannot break out of the quoted search term."""
    assert trigger_tag_query("pdf:sign") == 'tags:"pdf:sign"'
    assert trigger_tag_query('a"b\\c') == 'tags:"a\\"b\\\\c"'


def test_attachment_meta_coerces_numeric_string_sizes() -> None:
    """Zammad's string sizes become ints; ints and missing sizes are unchanged."""
    assert AttachmentMeta.model_validate({"id": 1, "size": "29935"}).size == 29935
    assert AttachmentMeta.model_validate({"id": 1, "size": 12}).size == 12
    assert AttachmentMeta.model_validate({"id": 1}).size is None
    with pytest.raises(ValueError, match="size"):
        AttachmentMeta.model_validate({"id": 1, "size": "big"})


def test_article_parses_capture_fields_from_the_fixture() -> None:
    """The by-ticket fixture exposes type, sender, updated_at, cc and message ids."""
    items = json.loads((_FIXTURES / "ticket_articles_by_ticket.json").read_text())
    article = Article.model_validate(items[0])
    assert article.type == "email"
    assert article.sender == "Customer"
    assert article.message_id == "<20261020075841.1@example.com>"
    assert article.in_reply_to is None
    assert article.cc is None
    assert article.updated_at is not None
    assert article.attachments is not None
    assert article.attachments[0].size == 29935
