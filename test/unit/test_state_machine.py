from __future__ import annotations

import asyncio

from zammad_pdf_archiver.domain.state_machine import (
    DONE_TAG,
    ERROR_TAG,
    PROCESSING_TAG,
    TRIGGER_TAG,
    apply_done,
    apply_error,
    apply_processing,
    should_process,
)


class _StubClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    async def add_tag(self, ticket_id: int, tag: str) -> None:
        self.calls.append(("add_tag", ticket_id, tag))

    async def remove_tag(self, ticket_id: int, tag: str) -> None:
        self.calls.append(("remove_tag", ticket_id, tag))


def test_should_process_trigger_present_done_missing() -> None:
    assert should_process([TRIGGER_TAG]) is True


def test_should_process_trigger_missing() -> None:
    assert should_process([]) is False


def test_should_process_done_present() -> None:
    assert should_process([TRIGGER_TAG, DONE_TAG]) is False


def test_should_process_none_tags() -> None:
    assert should_process(None) is False


def test_should_process_custom_trigger_tag() -> None:
    assert should_process(["pdf:archive"], trigger_tag="pdf:archive") is True
    assert should_process([TRIGGER_TAG], trigger_tag="pdf:archive") is False


def test_should_process_require_trigger_disabled() -> None:
    assert should_process([], require_trigger_tag=False) is True
    assert should_process([DONE_TAG], require_trigger_tag=False) is False


def test_apply_processing_transitions() -> None:
    async def run() -> None:
        client = _StubClient()
        await apply_processing(client, 123)
        assert client.calls == [
            ("remove_tag", 123, DONE_TAG),
            ("remove_tag", 123, ERROR_TAG),
            ("remove_tag", 123, TRIGGER_TAG),
            ("add_tag", 123, PROCESSING_TAG),
        ]

    asyncio.run(run())


def test_apply_processing_respects_custom_trigger_tag() -> None:
    async def run() -> None:
        client = _StubClient()
        await apply_processing(client, 123, trigger_tag="pdf:archive")
        assert client.calls == [
            ("remove_tag", 123, DONE_TAG),
            ("remove_tag", 123, ERROR_TAG),
            ("remove_tag", 123, "pdf:archive"),
            ("add_tag", 123, PROCESSING_TAG),
        ]

    asyncio.run(run())


def test_apply_done_transitions() -> None:
    async def run() -> None:
        client = _StubClient()
        await apply_done(client, 123)
        assert client.calls == [
            ("remove_tag", 123, PROCESSING_TAG),
            ("remove_tag", 123, ERROR_TAG),
            ("remove_tag", 123, TRIGGER_TAG),
            ("add_tag", 123, DONE_TAG),
        ]

    asyncio.run(run())


def test_apply_done_respects_custom_trigger_tag() -> None:
    async def run() -> None:
        client = _StubClient()
        await apply_done(client, 123, trigger_tag="pdf:archive")
        assert client.calls == [
            ("remove_tag", 123, PROCESSING_TAG),
            ("remove_tag", 123, ERROR_TAG),
            ("remove_tag", 123, "pdf:archive"),
            ("add_tag", 123, DONE_TAG),
        ]

    asyncio.run(run())


def test_apply_error_transitions_keep_trigger_default() -> None:
    async def run() -> None:
        client = _StubClient()
        await apply_error(client, 123)
        assert client.calls == [
            ("remove_tag", 123, PROCESSING_TAG),
            ("remove_tag", 123, DONE_TAG),
            ("add_tag", 123, TRIGGER_TAG),
            ("add_tag", 123, ERROR_TAG),
        ]

    asyncio.run(run())


def test_apply_error_transitions_drop_trigger() -> None:
    async def run() -> None:
        client = _StubClient()
        await apply_error(client, 123, keep_trigger=False)
        assert client.calls == [
            ("remove_tag", 123, PROCESSING_TAG),
            ("remove_tag", 123, DONE_TAG),
            ("remove_tag", 123, TRIGGER_TAG),
            ("add_tag", 123, ERROR_TAG),
        ]

    asyncio.run(run())


def test_apply_error_respects_custom_trigger_tag() -> None:
    async def run() -> None:
        client = _StubClient()
        await apply_error(client, 123, trigger_tag="pdf:archive")
        assert client.calls == [
            ("remove_tag", 123, PROCESSING_TAG),
            ("remove_tag", 123, DONE_TAG),
            ("add_tag", 123, "pdf:archive"),
            ("add_tag", 123, ERROR_TAG),
        ]

    asyncio.run(run())
