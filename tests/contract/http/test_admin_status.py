"""Pin the admission block of the administration status API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from chronikwerk.operations.scheduling import JournalAdmission
from chronikwerk.web.app import create_app
from tests.support.scheduling import SchedulingSpy
from tests.support.settings_factory import make_settings

_TOKEN = "admin-token-0123456789abcdef0123456789"


def _status(tmp_path, scheduler: SchedulingSpy | None) -> dict[str, object]:
    """Log in and return the admission block reported by the status API."""
    (tmp_path / "archive").mkdir()
    settings = make_settings(
        str(tmp_path / "archive"),
        overrides={
            "admin": {
                "enabled": True,
                "access_token": _TOKEN,
                "state_dir": str(tmp_path / "admin-state"),
            },
            "admission": {"max_pending": 7, "max_running": 3},
        },
    )
    client = TestClient(create_app(settings, scheduler=scheduler), base_url="https://testserver")
    login = client.post("/admin/login", data={"access_token": _TOKEN}, follow_redirects=False)
    assert login.status_code == 303
    response = client.get("/admin/api/v1/status")
    assert response.status_code == 200
    return response.json()["admission"]


def test_status_reports_scheduler_admission_counters(tmp_path) -> None:
    """A scheduling application reports its live admission counters and limits."""
    admission = JournalAdmission(pending=2, running=0, max_pending=5, max_running=2, closing=False)

    admission_status = _status(tmp_path, SchedulingSpy(admission=admission))

    assert admission_status == {
        "pending": 2,
        "running": 0,
        "max_pending": 5,
        "max_running": 2,
        "closing": False,
    }


def test_read_only_status_reports_configured_limits(tmp_path) -> None:
    """Without a scheduler the status reports idle counters and the configured limits."""
    admission_status = _status(tmp_path, None)

    assert admission_status == {
        "pending": 0,
        "running": 0,
        "max_pending": 7,
        "max_running": 3,
        "closing": False,
    }
