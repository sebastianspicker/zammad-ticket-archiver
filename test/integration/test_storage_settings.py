from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from zammad_pdf_archiver.adapters.storage.layout import build_filename_from_pattern
from zammad_pdf_archiver.app.jobs import process_ticket as process_ticket_module
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.config.settings import Settings


def _settings(storage_root: str, *, fsync: bool = True, atomic_write: bool = True) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root, "fsync": fsync, "atomic_write": atomic_write},
        }
    )


def _mock_happy_zammad(ticket_id: int = 123) -> None:
    respx.get(f"https://zammad.example.local/api/v1/tickets/{ticket_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": ticket_id,
                "number": "20240123",
                "title": "Example Ticket",
                "owner": {"login": "agent"},
                "updated_by": {"login": "fallback-agent"},
                "preferences": {
                    "custom_fields": {
                        "archive_user_mode": "owner",
                        "archive_path": "A > B > C",
                    }
                },
            },
        )
    )

    respx.get(
        "https://zammad.example.local/api/v1/tags",
        params={"object": "Ticket", "o_id": str(ticket_id)},
    ).mock(return_value=httpx.Response(200, json=["pdf:sign"]))

    respx.get(
        f"https://zammad.example.local/api/v1/ticket_articles/by_ticket/{ticket_id}"
    ).mock(return_value=httpx.Response(200, json=[]))

    respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    respx.post("https://zammad.example.local/api/v1/tags/add").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
        return_value=httpx.Response(200, json={"id": 999})
    )


def _expected_pdf_path(
    tmp_path: Path,
    *,
    settings: Settings,
    ticket_number: str,
    fixed_now: datetime,
) -> Path:
    filename = build_filename_from_pattern(
        settings.storage.path_policy.filename_pattern,
        ticket_number=ticket_number,
        timestamp_utc=fixed_now.date().isoformat(),
    )
    return tmp_path / "agent" / "A" / "B" / "C" / filename


def test_storage_fsync_can_be_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(str(tmp_path), fsync=False)
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "_now_utc", lambda: fixed_now)

    def _fsync(_: int) -> None:
        raise AssertionError("os.fsync must not be called when storage.fsync=false")

    monkeypatch.setattr(os, "fsync", _fsync)

    payload = {"ticket": {"id": 123}, "_request_id": "req-fsync-off-1"}
    with respx.mock:
        _mock_happy_zammad(ticket_id=123)
        asyncio.run(process_ticket("delivery-fsync-off-1", payload, settings))

    expected_pdf = _expected_pdf_path(
        tmp_path, settings=settings, ticket_number="20240123", fixed_now=fixed_now
    )
    assert expected_pdf.exists()


def test_storage_atomic_write_can_be_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(str(tmp_path), atomic_write=False)
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "_now_utc", lambda: fixed_now)

    import tempfile

    def _mkstemp(*args, **kwargs):  # noqa: ANN001 - test shim
        raise AssertionError("tempfile.mkstemp must not be called when storage.atomic_write=false")

    monkeypatch.setattr(tempfile, "mkstemp", _mkstemp)

    payload = {"ticket": {"id": 123}, "_request_id": "req-atomic-off-1"}
    with respx.mock:
        _mock_happy_zammad(ticket_id=123)
        asyncio.run(process_ticket("delivery-atomic-off-1", payload, settings))

    expected_pdf = _expected_pdf_path(
        tmp_path, settings=settings, ticket_number="20240123", fixed_now=fixed_now
    )
    assert expected_pdf.exists()
