"""Pin administration session teardown and operator retry behavior over HTTP."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from chronikwerk.operations.history import EmptyHistory
from chronikwerk.web.app import create_app
from tests.support.scheduling import SchedulingSpy
from tests.support.settings_factory import make_settings

_TOKEN = "admin-token-0123456789abcdef0123456789"
_COOKIE = "zpa_admin_session"


def _client(tmp_path: Path, scheduler: SchedulingSpy | None = None) -> tuple[TestClient, str]:
    """Create a logged-in administration client and return it with its CSRF token."""
    root = tmp_path.resolve()
    (root / "archive").mkdir()
    settings = make_settings(
        str(root / "archive"),
        overrides={
            "admin": {
                "enabled": True,
                "access_token": _TOKEN,
                "state_dir": str(root / "admin-state"),
            }
        },
    )
    client = TestClient(create_app(settings, scheduler=scheduler), base_url="https://testserver")
    login = client.post("/admin/login", data={"access_token": _TOKEN}, follow_redirects=False)
    assert login.status_code == 303
    page = client.get("/admin/configuration")
    match = re.search(r'<meta name="csrf-token" content="([^"]+)">', page.text)
    assert match is not None
    return client, match.group(1)


def test_api_session_delete_logs_out_and_clears_the_cookie(tmp_path: Path) -> None:
    """DELETE /session returns 204, expires the cookie and invalidates the server session."""
    client, csrf = _client(tmp_path)
    assert client.get("/admin/api/v1/status").status_code == 200

    response = client.delete("/admin/api/v1/session", headers={"X-CSRF-Token": csrf})

    assert response.status_code == 204
    assert _COOKIE in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
    stale = client.get("/admin/api/v1/status")
    assert stale.status_code == 401
    assert stale.json()["code"] == "admin_session_required"


def test_api_session_delete_requires_csrf(tmp_path: Path) -> None:
    """DELETE /session without the CSRF header is refused and leaves the session intact."""
    client, _csrf = _client(tmp_path)

    response = client.delete("/admin/api/v1/session")

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_invalid"
    assert client.get("/admin/api/v1/status").status_code == 200


def test_form_logout_clears_cookie_and_redirects_to_login(tmp_path: Path) -> None:
    """POST /logout with a valid CSRF token ends the session and redirects to the login page."""
    client, csrf = _client(tmp_path)

    response = client.post("/admin/logout", data={"csrf_token": csrf}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.get("/admin/api/v1/status").status_code == 401


def test_form_logout_with_bad_csrf_keeps_the_session(tmp_path: Path) -> None:
    """POST /logout with a wrong CSRF token redirects to login without ending the session."""
    client, _csrf = _client(tmp_path)

    response = client.post("/admin/logout", data={"csrf_token": "wrong"}, follow_redirects=False)

    assert response.status_code == 303
    assert "set-cookie" not in response.headers
    assert client.get("/admin/api/v1/status").status_code == 200


def test_retry_api_schedules_and_records_ticket_and_request_id(tmp_path: Path) -> None:
    """An acknowledged retry is accepted with 202 and handed to the scheduler."""
    spy = SchedulingSpy()
    client, csrf = _client(tmp_path, spy)

    response = client.post(
        "/admin/api/v1/jobs/42/retry",
        headers={"X-CSRF-Token": csrf},
        json={"acknowledge_overwrite": True},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["ticket_id"] == 42
    assert spy.retries == [(42, body["request_id"])]


def test_retry_api_requires_overwrite_acknowledgement(tmp_path: Path) -> None:
    """A retry without overwrite acknowledgement is a 422 and is never scheduled."""
    spy = SchedulingSpy()
    client, csrf = _client(tmp_path, spy)

    response = client.post(
        "/admin/api/v1/jobs/42/retry",
        headers={"X-CSRF-Token": csrf},
        json={"acknowledge_overwrite": False},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "overwrite_acknowledgement_required"
    assert spy.retries == []


def test_retry_api_rejects_invalid_ticket_ids(tmp_path: Path) -> None:
    """Zero and non-numeric ticket ids fail path validation with 422 and no scheduling."""
    spy = SchedulingSpy()
    client, csrf = _client(tmp_path, spy)

    for ticket in ("0", "-1", "abc"):
        response = client.post(
            f"/admin/api/v1/jobs/{ticket}/retry",
            headers={"X-CSRF-Token": csrf},
            json={"acknowledge_overwrite": True},
        )
        assert response.status_code == 422, ticket
    assert spy.retries == []


def test_retry_api_reports_capacity_exhaustion_with_retry_after(tmp_path: Path) -> None:
    """A rejecting scheduler yields 503 job_capacity_exhausted with a Retry-After hint."""
    spy = SchedulingSpy(accept=False)
    client, csrf = _client(tmp_path, spy)

    response = client.post(
        "/admin/api/v1/jobs/42/retry",
        headers={"X-CSRF-Token": csrf},
        json={"acknowledge_overwrite": True},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "job_capacity_exhausted"
    assert response.headers["retry-after"] == "1"
    assert len(spy.retries) == 1


def test_retry_api_requires_csrf_and_session(tmp_path: Path) -> None:
    """A retry without CSRF is 403 and one without a session is 401; neither schedules."""
    spy = SchedulingSpy()
    client, _csrf = _client(tmp_path, spy)
    payload = {"acknowledge_overwrite": True}

    no_csrf = client.post("/admin/api/v1/jobs/42/retry", json=payload)
    anonymous = TestClient(client.app, base_url="https://testserver").post(
        "/admin/api/v1/jobs/42/retry", json=payload
    )

    assert no_csrf.status_code == 403
    assert no_csrf.json()["code"] == "csrf_invalid"
    assert anonymous.status_code == 401
    assert spy.retries == []


class _LoopProbeHistory(EmptyHistory):
    """Empty history that records whether each read ran on an event-loop thread."""

    def __init__(self, items: list[dict[str, Any]] | None = None) -> None:
        self.on_loop: list[bool] = []
        self.items = items or []

    def read(
        self,
        limit: int,
        ticket_id: int | None = None,
        *,
        before_id: int | None = None,
        statuses: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.on_loop.append(False)
        else:
            self.on_loop.append(True)
        return self.items


def test_admin_history_reads_run_off_the_event_loop(tmp_path: Path) -> None:
    """Journal reads block, so every admin view reads the history in a worker thread."""
    history = _LoopProbeHistory()
    client, _csrf = _client(tmp_path, SchedulingSpy(history=history))

    for path in ("/admin", "/admin/jobs", "/admin/jobs/7", "/admin/api/v1/jobs"):
        assert client.get(path).status_code == 200, path

    assert history.on_loop == [False, False, False, False]


def test_jobs_page_gives_coalesced_and_skipped_rows_their_tones(tmp_path: Path) -> None:
    items = [
        {"id": str(index), "status": status, "ticket_id": index, "created_at": 0.0}
        for index, status in enumerate(("coalesced", "skipped"), start=1)
    ]
    client, _csrf = _client(tmp_path, SchedulingSpy(history=_LoopProbeHistory(items)))

    page = client.get("/admin/jobs").text

    assert '<span class="notation notation--pending">coalesced</span>' in page
    assert '<span class="notation notation--idle">skipped</span>' in page
