"""Verify signed-webhook behavior through the public HTTP application."""

from __future__ import annotations

from fastapi.testclient import TestClient

from chronikwerk.web.app import create_app
from tests.support.http_security_test_helpers import post_ingest
from tests.support.settings_factory import make_settings


def test_missing_or_malformed_signature_is_rejected_before_ingest(tmp_path) -> None:
    client = TestClient(create_app(make_settings(str(tmp_path), secret="test-secret")))

    missing = client.post("/ingest", content=b'{"ticket_id":1}')
    malformed = post_ingest(client, b'{"ticket_id":1}', "sha256=not-hex")

    assert missing.status_code == 403
    assert malformed.status_code == 403


def test_blank_webhook_secret_fails_closed_as_misconfigured(tmp_path) -> None:
    client = TestClient(create_app(make_settings(str(tmp_path), secret="   ")))

    response = client.post("/ingest", content=b'{"ticket_id":1}')

    assert response.status_code == 503
    assert response.json()["code"] == "webhook_auth_not_configured"
