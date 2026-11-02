"""Run one Zammad-signed webhook through the composed service to a sealed, verified revision.

Every Zammad body comes from ``tests/fixtures/zammad`` and every call goes through the real
gateway and transport, so the test pins the wire contract end to end: HMAC-SHA1 signature,
``expand=true`` ticket fetch, top-level custom attributes, original email and attachment
downloads, and the DELETE verb for tag removal. The TSA is the synthetic one over HTTP. The
journal, catalog, store, seal and pipeline are the ones ``composition`` builds.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import stat
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from chronikwerk import composition
from chronikwerk.archiving.commit_checks import seal_signer_fingerprint
from chronikwerk.configuration.models import Settings
from chronikwerk.preservation.seal import SealOptions, load_trust_anchors
from chronikwerk.preservation.verify import verify_package_directory
from tests.support.commit_harness import restore_modes
from tests.support.fake_capture import ATTACHMENT, EML
from tests.support.hmac_test_helpers import sign_zammad_native
from tests.support.preservation_settings import RECORD_CLASS, make_runtime_settings
from tests.support.tsa import mock_tsa
from tests.support.zammad_fixtures import load_zammad_fixture

_API = "https://zammad.example.local/api/v1"
_SECRET = "test-webhook-hmac-secret-0123456789abcdef"
_WAIT_SECONDS = 120.0


class _FakeZammad:
    """Serve the documented Zammad responses for ticket 42 and keep the tag set and notes."""

    def __init__(self) -> None:
        self.ticket = {
            **load_zammad_fixture("ticket_show_expand.json"),
            "archive_record_class": RECORD_CLASS,
        }
        self.tags: set[str] = set(load_zammad_fixture("tags.json")["tags"])
        self.notes: list[dict[str, Any]] = []
        self.hold_tags = False

    async def _list_tags(self, _request: httpx.Request) -> httpx.Response:
        if self.hold_tags:
            # Zammad never answers: the worker is stopped before it learns anything.
            await asyncio.Event().wait()
        return httpx.Response(200, json={"tags": sorted(self.tags)})

    def _add_tag(self, request: httpx.Request) -> httpx.Response:
        self.tags.add(json.loads(request.content)["item"])
        return httpx.Response(200, json=True)

    def _remove_tag(self, request: httpx.Request) -> httpx.Response:
        self.tags.discard(json.loads(request.content)["item"])
        return httpx.Response(200, json=True)

    def _note(self, request: httpx.Request) -> httpx.Response:
        note = json.loads(request.content)
        self.notes.append(note)
        return httpx.Response(201, json={"id": 900 + len(self.notes), "ticket_id": 42, **note})

    def install(self, mock: respx.MockRouter) -> dict[str, respx.Route]:
        """Register the routes on ``mock`` and return them by name."""
        return {
            "ticket": mock.get(f"{_API}/tickets/42", params={"expand": "true"}).respond(
                200, json=self.ticket
            ),
            "articles": mock.get(f"{_API}/ticket_articles/by_ticket/42").respond(
                200, json=load_zammad_fixture("ticket_articles_by_ticket.json")
            ),
            "plain": mock.get(f"{_API}/ticket_article_plain/101").respond(
                200, content=EML, headers={"Content-Type": "message/rfc822"}
            ),
            "attachment": mock.get(f"{_API}/ticket_attachment/42/101/501").respond(
                200, content=ATTACHMENT, headers={"Content-Type": "image/png"}
            ),
            "tags": mock.get(f"{_API}/tags").mock(side_effect=self._list_tags),
            "me": mock.get(f"{_API}/users/me").respond(
                200, json={"id": 9, "login": "chronikwerk-api", "firstname": "Chronikwerk"}
            ),
            "remove": mock.delete(f"{_API}/tags/remove").mock(side_effect=self._remove_tag),
            "add": mock.post(f"{_API}/tags/add").mock(side_effect=self._add_tag),
            "note": mock.post(f"{_API}/ticket_articles").mock(side_effect=self._note),
        }


@pytest.fixture
def archive_dir(tmp_path: Path) -> Iterator[Path]:
    """Return the archive root and make published revisions removable afterwards."""
    yield tmp_path / "archive"
    restore_modes(tmp_path / "archive")


def _settings(tmp_path: Path, *, shutdown_timeout: float) -> Settings:
    """Return runtime settings below ``tmp_path`` with the given drain timeout."""
    return make_runtime_settings(
        tmp_path,
        secret=_SECRET,
        overrides={"admission": {"shutdown_timeout_seconds": shutdown_timeout}},
    )


def _use(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """Make the composition load ``settings`` and leave logging alone."""
    monkeypatch.setattr(composition, "load_settings", lambda: settings)
    monkeypatch.setattr(composition, "configure_logging", lambda **_kwargs: None)


def _post_webhook(client: TestClient) -> None:
    """Post the default trigger payload, signed as Zammad signs it."""
    body = json.dumps(load_zammad_fixture("webhook_trigger_payload.json")).encode("utf-8")
    response = client.post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": sign_zammad_native(body, _SECRET),
            "X-Zammad-Delivery": "delivery-e2e-1",
            "X-Zammad-Trigger": "Archive ticket",
        },
    )
    assert response.status_code == 202, response.text


def _journal_row(settings: Settings) -> sqlite3.Row:
    """Read the single journaled request without going through the service's connection."""
    connection = sqlite3.connect(f"file:{settings.journal.path}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        (row,) = connection.execute("SELECT * FROM requests").fetchall()
        return row
    finally:
        connection.close()


def _wait_for_state(
    settings: Settings, wanted: set[str], *, require_reported: bool = False
) -> sqlite3.Row:
    """Wait for the requested state and, when required, a settled Zammad update."""
    deadline = time.monotonic() + _WAIT_SECONDS
    while True:
        row = _journal_row(settings)
        reported = not require_reported or row["zammad_status"] in {"done", "failed"}
        ready = row["state"] in wanted and (row["state"] == "failed" or reported)
        if ready or time.monotonic() > deadline:
            return row
        time.sleep(0.05)


def _published_directory(archive_dir: Path, revision_id: str) -> Path:
    """Return the single published directory of the first revision below the class."""
    (directory,) = sorted(archive_dir.glob(f"{RECORD_CLASS}/**/r0001_{revision_id}"))
    return directory


def _assert_sealed_revision(settings: Settings, archive_dir: Path, revision_id: str) -> Path:
    """Assert the revision is under the class prefix, read-only and verifies pinned."""
    directory = _published_directory(archive_dir, revision_id)
    assert directory.relative_to(archive_dir).parts[:4] == (
        RECORD_CLASS,
        "alice",
        "Customers",
        "ACME",
    )
    assert stat.S_IMODE(directory.stat().st_mode) & 0o222 == 0
    assert all(stat.S_IMODE(path.stat().st_mode) & 0o222 == 0 for path in directory.rglob("*"))
    assert settings.verify.trust_anchors_path is not None
    assert settings.seal.pfx_path is not None and settings.seal.pfx_password is not None
    seal = SealOptions(settings.seal.pfx_path, settings.seal.pfx_password.get_secret_value())
    report = asyncio.run(
        verify_package_directory(
            directory,
            trust_anchors=load_trust_anchors(settings.verify.trust_anchors_path),
            expected_signer_sha256={seal_signer_fingerprint(seal)},
        )
    )
    assert report.passed, report.checks
    return directory


def _assert_reported(zammad: _FakeZammad, revision_id: str) -> None:
    """Assert the done tags and one internal success note naming the revision."""
    assert zammad.tags == {"pdf:signed"}
    (note,) = zammad.notes
    assert (note["ticket_id"], note["internal"]) == (42, True)
    assert revision_id in note["body"]


def test_signed_webhook_publishes_a_sealed_verified_revision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, archive_dir: Path
) -> None:
    settings = _settings(tmp_path, shutdown_timeout=120)
    _use(monkeypatch, settings)
    zammad = _FakeZammad()

    with respx.mock(assert_all_called=False) as mock:
        routes = zammad.install(mock)
        tsa = mock_tsa(mock)
        _settings_used, app = composition.build_runtime_application()
        with TestClient(app) as client:
            _post_webhook(client)
            row = _wait_for_state(settings, {"complete", "failed"}, require_reported=True)

    assert row["state"] == "complete", dict(row)
    assert row["zammad_status"] == "done"
    _assert_sealed_revision(settings, archive_dir, row["revision_id"])
    _assert_reported(zammad, row["revision_id"])
    assert routes["plain"].call_count >= 1
    assert routes["attachment"].call_count >= 1
    # Capture needs the service login once, to keep its own notes out of the source digest.
    assert routes["me"].call_count == 1
    assert tsa.call_count >= 1
    assert list(settings.capture.temp_dir.iterdir()) == []


def test_request_journaled_before_a_restart_completes_after_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, archive_dir: Path
) -> None:
    """Acceptance check A03: the journal, not process memory, carries the request."""
    zammad = _FakeZammad()
    zammad.hold_tags = True

    with respx.mock(assert_all_called=False) as mock:
        zammad.install(mock)
        mock_tsa(mock)
        first = _settings(tmp_path, shutdown_timeout=1)
        _use(monkeypatch, first)
        _settings_used, app = composition.build_runtime_application()
        with TestClient(app) as client:
            _post_webhook(client)
            interrupted = _wait_for_state(first, {"collecting"})
        # Shutdown cancelled the stuck worker; the row keeps its state for the next start.
        assert interrupted["state"] == "collecting"
        assert _journal_row(first)["state"] == "collecting"
        assert zammad.notes == [] and not list(archive_dir.rglob("manifest.json"))

        zammad.hold_tags = False
        second = _settings(tmp_path, shutdown_timeout=120)
        _use(monkeypatch, second)
        _settings_used, app = composition.build_runtime_application()
        with TestClient(app) as client:
            row = _wait_for_state(second, {"complete", "failed"})

    assert row["state"] == "complete", dict(row)
    assert row["request_id"] == interrupted["request_id"]
    _assert_sealed_revision(second, archive_dir, row["revision_id"])
    _assert_reported(zammad, row["revision_id"])
