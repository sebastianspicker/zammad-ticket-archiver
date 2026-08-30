"""Verify authenticated administration configuration persistence over HTTP."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from chronikwerk.web.app import create_app
from tests.support.settings_factory import make_settings


def _client(tmp_path) -> TestClient:
    """Create an enabled administration client with isolated durable state."""
    settings = make_settings(
        str(tmp_path),
        secret="test-webhook-secret-0123456789abcdef",
        overrides={
            "admin": {
                "enabled": True,
                "access_token": "admin-token-0123456789abcdef0123456789",
                "state_dir": str(tmp_path / "admin-state"),
            }
        },
    )
    return TestClient(create_app(settings), base_url="https://testserver")


def _login_and_csrf(client: TestClient) -> str:
    """Authenticate and return the session's anti-forgery token."""
    login = client.post(
        "/admin/login",
        data={
            "access_token": "admin-token-0123456789abcdef0123456789",
            "next": "/admin/configuration",
        },
        follow_redirects=False,
    )
    assert login.status_code == 303
    page = client.get("/admin/configuration")
    match = re.search(r'<meta name="csrf-token" content="([^"]+)">', page.text)
    assert match is not None
    return match.group(1)


def test_admin_api_validates_stages_and_reports_restart_state(tmp_path) -> None:
    client = _client(tmp_path)
    csrf = _login_and_csrf(client)

    before = client.get("/admin/api/v1/config")
    assert before.status_code == 200
    revision = before.json()["revision"]
    assert before.json()["restart_required"] is False

    preview = client.post(
        "/admin/api/v1/config/validate",
        headers={"X-CSRF-Token": csrf},
        json={"values": {"workflow.trigger_tag": "archive:next"}},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["diff"] == [
        {"path": "workflow.trigger_tag", "before": "pdf:sign", "after": "archive:next"}
    ]

    staged = client.put(
        "/admin/api/v1/config/staged",
        headers={"X-CSRF-Token": csrf, "If-Match": revision},
        json={"overlay": {"pdf": {"locale": "en-GB"}}},
    )
    assert staged.status_code == 200
    assert staged.json()["restart_required"] is True

    after = client.get("/admin/api/v1/config").json()
    assert after["staged_revision"] == staged.json()["revision"]
    assert after["restart_required"] is True

    conflict = client.put(
        "/admin/api/v1/config/staged",
        headers={"X-CSRF-Token": csrf, "If-Match": revision},
        json={"overlay": {"pdf": {"locale": "de-DE"}}},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "config_revision_conflict"


def test_admin_config_rejects_security_changes_without_acknowledgement(tmp_path) -> None:
    client = _client(tmp_path)
    csrf = _login_and_csrf(client)

    response = client.post(
        "/admin/api/v1/config/validate",
        headers={"X-CSRF-Token": csrf},
        json={"values": {"hardening.transport.trust_env": True}},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "security_acknowledgement_required"
