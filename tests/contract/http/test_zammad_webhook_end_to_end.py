"""Run one Zammad-signed webhook through the composed service against a mocked Zammad API.

Every Zammad body comes from ``tests/fixtures/zammad`` and every call goes through the real
gateway and transport, so the test pins the wire contract end to end: HMAC-SHA1 signature,
``expand=true`` ticket fetch, top-level custom attributes, the trigger agent from the webhook
body, and the DELETE verb for tag removal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import respx
from fastapi.testclient import TestClient

from chronikwerk import composition
from tests.support.hmac_test_helpers import sign_zammad_native
from tests.support.settings_factory import make_settings
from tests.support.zammad_fixtures import load_zammad_fixture

_API = "https://zammad.example.local/api/v1"
_SECRET = "test-webhook-hmac-secret-0123456789abcdef"


def _mock_zammad(mock: respx.MockRouter, ticket: dict[str, Any]) -> dict[str, respx.Route]:
    """Serve the documented Zammad responses for ticket 42 and record mutations."""
    return {
        "ticket": mock.get(f"{_API}/tickets/42", params={"expand": "true"}).respond(
            200, json=ticket
        ),
        "tags": mock.get(f"{_API}/tags").respond(200, json=load_zammad_fixture("tags.json")),
        "articles": mock.get(f"{_API}/ticket_articles/by_ticket/42").respond(
            200, json=load_zammad_fixture("ticket_articles_by_ticket.json")
        ),
        "me": mock.get(f"{_API}/users/me").respond(
            200, json={"id": 9, "login": "chronikwerk-api", "firstname": "Chronikwerk"}
        ),
        "remove": mock.delete(f"{_API}/tags/remove").respond(200, json=True),
        "add": mock.post(f"{_API}/tags/add").respond(200, json=True),
        "note": mock.post(f"{_API}/ticket_articles").respond(201, json={"id": 900}),
    }


def _deliver(monkeypatch, tmp_path: Path, ticket: dict[str, Any]) -> dict[str, respx.Route]:
    """Post the default trigger payload, signed as Zammad signs it, and drain the job."""
    # A generous drain timeout keeps a slow first PDF render from being cancelled at shutdown.
    settings = make_settings(
        str(tmp_path), secret=_SECRET, overrides={"admission": {"shutdown_timeout_seconds": 120}}
    )
    monkeypatch.setattr(composition, "load_settings", lambda: settings)
    monkeypatch.setattr(composition, "configure_logging", lambda **_kwargs: None)
    body = json.dumps(load_zammad_fixture("webhook_trigger_payload.json")).encode("utf-8")

    with respx.mock(assert_all_called=False) as mock:
        routes = _mock_zammad(mock, ticket)
        _settings, app = composition.build_runtime_application()
        with TestClient(app) as client:
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
        # Leaving the client runs the lifespan shutdown, which drains admitted work.
    return routes


def _tag_items(route: respx.Route) -> list[str]:
    """Return the tag names sent to one tag-mutation route, in call order."""
    return [json.loads(call.request.content)["item"] for call in route.calls]


def test_signed_zammad_webhook_archives_under_owner_and_projects_tags(
    monkeypatch, tmp_path: Path
) -> None:
    routes = _deliver(monkeypatch, tmp_path, load_zammad_fixture("ticket_show_expand.json"))

    (pdf,) = sorted(tmp_path.rglob("*.pdf"))
    (sidecar,) = sorted(tmp_path.rglob("*.pdf.json"))
    assert pdf.parent == tmp_path / "alice" / "Customers" / "ACME"
    assert pdf.name.startswith("Ticket-31042_")
    assert pdf.read_bytes().startswith(b"%PDF-")
    assert json.loads(sidecar.read_text(encoding="utf-8"))["ticket_number"] == "31042"
    assert routes["ticket"].call_count == 1
    assert routes["me"].call_count == 0  # owner mode never needs the service login
    assert "pdf:processing" in _tag_items(routes["remove"])
    assert _tag_items(routes["add"]) == ["pdf:processing", "pdf:signed"]
    note = json.loads(routes["note"].calls.last.request.content)
    assert (note["ticket_id"], note["internal"]) == (42, True)


def test_current_agent_mode_archives_under_the_trigger_agent(monkeypatch, tmp_path: Path) -> None:
    ticket = load_zammad_fixture("ticket_show_expand.json")
    ticket["archive_user_mode"] = "current_agent"

    routes = _deliver(monkeypatch, tmp_path, ticket)

    (pdf,) = sorted(tmp_path.rglob("*.pdf"))
    assert pdf.parent == tmp_path / "carol" / "Customers" / "ACME"
    assert routes["me"].call_count == 1
