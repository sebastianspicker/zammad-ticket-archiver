from __future__ import annotations

from zammad_pdf_archiver.app.jobs.ticket_notes import concise_exc_message as _concise_exc_message, success_note_html as _success_note_html
from zammad_pdf_archiver.config.redact import REDACTED_VALUE


def test_success_note_html_escapes_untrusted_values() -> None:
    html = _success_note_html(
        storage_dir='/tmp/archive/<script>alert("x")</script>&',
        filename='evil"><img src=x onerror=alert(1)>.pdf',
        sidecar_path="/tmp/archive/file.pdf.json?<x>",
        size_bytes=123,
        sha256_hex="ab" * 32,
        request_id="<b>req</b>",
        delivery_id='<svg/onload=alert("d")>',
        timestamp_utc="2026-02-07T18:00:00Z",
    )

    assert "<script>" not in html
    assert "<img" not in html
    assert "<svg" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html
    assert "&lt;svg" in html


def test_concise_exc_message_redacts_secrets() -> None:
    msg = _concise_exc_message(
        RuntimeError("Authorization: Bearer abc123 token=qwerty api_token=topsecret")
    )

    assert "abc123" not in msg
    assert "qwerty" not in msg
    assert "topsecret" not in msg
    assert REDACTED_VALUE in msg
