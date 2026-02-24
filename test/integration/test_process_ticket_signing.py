from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from zammad_pdf_archiver._version import VERSION
from zammad_pdf_archiver.adapters.storage.layout import build_filename_from_pattern
from zammad_pdf_archiver.app.jobs import process_ticket as process_ticket_module
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.config.settings import Settings

pytest.importorskip("pyhanko", reason="Signing integration requires pyHanko")


def _write_test_pfx(path: Path, password: str) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Integration Test Signer")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key=key, algorithm=hashes.SHA256())
    )

    pfx = pkcs12.serialize_key_and_certificates(
        name=b"test-signer",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode("utf-8")),
    )
    path.write_bytes(pfx)
    return cert.fingerprint(hashes.SHA256()).hex()


def _test_settings(storage_root: str, *, pfx_path: Path, password: str) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root},
            "signing": {
                "enabled": True,
                "pfx_path": str(pfx_path),
                "pfx_password": password,
            },
        }
    )


def _test_settings_with_unreachable_tsa(
    storage_root: str, *, pfx_path: Path, password: str, tsa_url: str
) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root},
            "signing": {
                "enabled": True,
                "pfx_path": str(pfx_path),
                "pfx_password": password,
                "timestamp": {
                    "enabled": True,
                    "rfc3161": {"tsa_url": tsa_url, "timeout_seconds": 0.1},
                },
            },
        }
    )


def test_process_ticket_signing_writes_signed_pdf_and_audit_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfx_path = tmp_path / "test.pfx"
    expected_fingerprint = _write_test_pfx(pfx_path, password="secret")
    settings = _test_settings(str(tmp_path), pfx_path=pfx_path, password="secret")

    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "_now_utc", lambda: fixed_now)

    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-sign-1",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 123,
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
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=["pdf:sign"]))

        respx.get("https://zammad.example.local/api/v1/ticket_articles/by_ticket/123").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "created_at": "2026-02-07T11:59:00Z",
                        "internal": False,
                        "subject": "Hello",
                        "body": "<p>Hello World</p>",
                        "content_type": "text/html",
                        "from": "customer@example.invalid",
                        "attachments": [],
                    }
                ],
            )
        )

        respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        respx.post("https://zammad.example.local/api/v1/tags/add").mock(
            return_value=httpx.Response(200, json={"success": True})
        )

        respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
            return_value=httpx.Response(
                200,
                json={"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"},
            )
        )

        asyncio.run(process_ticket("delivery-sign-1", payload, settings))

        date_iso = fixed_now.date().isoformat()
        expected_filename = build_filename_from_pattern(
            settings.storage.path_policy.filename_pattern,
            ticket_number="20240123",
            timestamp_utc=date_iso,
        )
        expected_pdf_path = tmp_path / "agent" / "A" / "B" / "C" / expected_filename
        expected_sidecar_path = expected_pdf_path.parent / (expected_pdf_path.name + ".json")

        assert expected_pdf_path.exists()
        assert expected_sidecar_path.exists()

        pdf_bytes = expected_pdf_path.read_bytes()
        assert pdf_bytes.startswith(b"%PDF")
        assert b"/ByteRange" in pdf_bytes

        audit = json.loads(expected_sidecar_path.read_text("utf-8"))
        assert audit["signing"]["enabled"] is True
        assert audit["signing"]["tsa_used"] is False
        assert audit["signing"]["cert_fingerprint"] == expected_fingerprint


def test_process_ticket_signing_with_unreachable_tsa_is_transient_and_keeps_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfx_path = tmp_path / "test.pfx"
    _write_test_pfx(pfx_path, password="secret")
    tsa_url = "https://tsa.test/rfc3161"
    settings = _test_settings_with_unreachable_tsa(
        str(tmp_path),
        pfx_path=pfx_path,
        password="secret",
        tsa_url=tsa_url,
    )

    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "_now_utc", lambda: fixed_now)

    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-sign-tsa-err-1",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        respx.post(tsa_url).mock(side_effect=httpx.ConnectError("boom"))

        respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 123,
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
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=["pdf:sign"]))

        respx.get("https://zammad.example.local/api/v1/ticket_articles/by_ticket/123").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "created_at": "2026-02-07T11:59:00Z",
                        "internal": False,
                        "subject": "Hello",
                        "body": "<p>Hello World</p>",
                        "content_type": "text/html",
                        "from": "customer@example.invalid",
                        "attachments": [],
                    }
                ],
            )
        )

        remove_tag_route = respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        add_tag_route = respx.post("https://zammad.example.local/api/v1/tags/add").mock(
            return_value=httpx.Response(200, json={"success": True})
        )

        article_route = respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
            return_value=httpx.Response(
                200,
                json={"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"},
            )
        )

        asyncio.run(process_ticket("delivery-sign-tsa-err-1", payload, settings))

        removed = {
            json.loads(call.request.content.decode("utf-8"))["item"]
            for call in remove_tag_route.calls
        }
        added = {
            json.loads(call.request.content.decode("utf-8"))["item"]
            for call in add_tag_route.calls
        }

        assert "pdf:processing" in removed  # cleanup
        assert "pdf:sign" in added  # transient: keep trigger for retries
        assert "pdf:error" in added

        assert article_route.called
        req = json.loads(article_route.calls[0].request.content.decode("utf-8"))
        assert f"PDF archiver error ({VERSION})" in req["subject"]
        assert "Transient" in req["body"]


def test_process_ticket_signing_with_invalid_pfx_password_is_permanent_and_drops_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfx_path = tmp_path / "test.pfx"
    _write_test_pfx(pfx_path, password="secret")
    settings = _test_settings(str(tmp_path), pfx_path=pfx_path, password="wrong-password")

    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "_now_utc", lambda: fixed_now)

    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-sign-bad-pass-1",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 123,
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
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=["pdf:sign"]))

        respx.get("https://zammad.example.local/api/v1/ticket_articles/by_ticket/123").mock(
            return_value=httpx.Response(200, json=[])
        )

        remove_tag_route = respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        add_tag_route = respx.post("https://zammad.example.local/api/v1/tags/add").mock(
            return_value=httpx.Response(200, json={"success": True})
        )

        article_route = respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
            return_value=httpx.Response(200, json={"id": 999})
        )

        asyncio.run(process_ticket("delivery-sign-bad-pass-1", payload, settings))

        assert list(tmp_path.rglob("*.pdf")) == []
        assert list(tmp_path.rglob("*.pdf.json")) == []

        removed = {
            json.loads(call.request.content.decode("utf-8"))["item"]
            for call in remove_tag_route.calls
        }
        added = {
            json.loads(call.request.content.decode("utf-8"))["item"]
            for call in add_tag_route.calls
        }

        assert "pdf:processing" in added
        assert "pdf:done" not in added
        assert "pdf:error" in added
        assert "pdf:sign" not in added

        assert "pdf:processing" in removed
        assert "pdf:sign" in removed

        assert article_route.called
        req = json.loads(article_route.calls[0].request.content.decode("utf-8"))
        assert f"PDF archiver error ({VERSION})" in req["subject"]
        assert "Permanent" in req["body"]
        assert "PKCS#12" in req["body"]


def test_process_ticket_signing_with_tsa_http_503_is_transient_and_keeps_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfx_path = tmp_path / "test.pfx"
    _write_test_pfx(pfx_path, password="secret")
    tsa_url = "https://tsa.test/rfc3161"
    settings = _test_settings_with_unreachable_tsa(
        str(tmp_path),
        pfx_path=pfx_path,
        password="secret",
        tsa_url=tsa_url,
    )

    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "_now_utc", lambda: fixed_now)

    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-sign-tsa-503-1",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        respx.post(tsa_url).mock(return_value=httpx.Response(503))

        respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 123,
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
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=["pdf:sign"]))

        respx.get("https://zammad.example.local/api/v1/ticket_articles/by_ticket/123").mock(
            return_value=httpx.Response(200, json=[])
        )

        remove_tag_route = respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        add_tag_route = respx.post("https://zammad.example.local/api/v1/tags/add").mock(
            return_value=httpx.Response(200, json={"success": True})
        )

        article_route = respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
            return_value=httpx.Response(200, json={"id": 999})
        )

        asyncio.run(process_ticket("delivery-sign-tsa-503-1", payload, settings))

        removed = {
            json.loads(call.request.content.decode("utf-8"))["item"]
            for call in remove_tag_route.calls
        }
        added = {
            json.loads(call.request.content.decode("utf-8"))["item"]
            for call in add_tag_route.calls
        }

        assert "pdf:processing" in removed
        assert "pdf:sign" in added
        assert "pdf:error" in added

        assert article_route.called
        req = json.loads(article_route.calls[0].request.content.decode("utf-8"))
        assert f"PDF archiver error ({VERSION})" in req["subject"]
        assert "Transient" in req["body"]
        assert "HTTP 503" in req["body"]
