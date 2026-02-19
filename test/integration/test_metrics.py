from __future__ import annotations

import json
import re

import httpx
import respx
from fastapi.testclient import TestClient

from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.state_machine import TRIGGER_TAG


def _test_settings(storage_root: str) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root},
            "observability": {"metrics_enabled": True},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": True,
                }
            },
        }
    )


def _test_settings_metrics_disabled(storage_root: str) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": True,
                }
            },
        }
    )


_METRIC_LINE_RE = re.compile(r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*) (?P<value>[-+0-9.eE]+)$")


def _metric_value(text: str, name: str) -> float:
    for line in text.splitlines():
        match = _METRIC_LINE_RE.match(line)
        if match and match.group("name") == name:
            return float(match.group("value"))
    raise AssertionError(f"metric {name!r} not found in /metrics output")


def test_metrics_endpoint_returns_prometheus_text(tmp_path) -> None:
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    assert "processed_total" in resp.text


def test_metrics_endpoint_is_not_exposed_when_disabled(tmp_path) -> None:
    app = create_app(_test_settings_metrics_disabled(str(tmp_path)))
    client = TestClient(app)

    resp = client.get("/metrics")
    assert resp.status_code == 404


def test_metrics_requires_bearer_when_configured(tmp_path) -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": str(tmp_path)},
            "observability": {"metrics_enabled": True, "metrics_bearer_token": "secret-token"},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": True,
                }
            },
        }
    )
    app = create_app(settings)
    client = TestClient(app)

    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert (
        client.get("/metrics", headers={"Authorization": "Bearer secret-token"}).status_code == 200
    )


def test_ingest_success_increments_processed_total(tmp_path) -> None:
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    before = _metric_value(client.get("/metrics").text, "processed_total")

    payload = {"ticket": {"id": 123}, "user": {"login": "agent-from-webhook"}}
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    with respx.mock:
        respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 123,
                    "number": "20240123",
                    "owner": {"login": "agent"},
                    "updated_by": {"login": "fallback-agent"},
                    "preferences": {
                        "custom_fields": {
                            "archive_user_mode": "owner",
                            "archive_path": ["A", "B", "C"],
                        }
                    },
                },
            )
        )

        respx.get(
            "https://zammad.example.local/api/v1/tags",
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=[TRIGGER_TAG]))

        respx.get("https://zammad.example.local/api/v1/ticket_articles/by_ticket/123").mock(
            return_value=httpx.Response(200, json=[])
        )

        respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        respx.post("https://zammad.example.local/api/v1/tags/add").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
            return_value=httpx.Response(200, json={"id": 999})
        )

        resp = client.post(
            "/ingest",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Zammad-Delivery": "delivery-metrics-20260207-0001",
            },
        )

    assert resp.status_code == 202

    after = _metric_value(client.get("/metrics").text, "processed_total")
    assert after == before + 1.0
