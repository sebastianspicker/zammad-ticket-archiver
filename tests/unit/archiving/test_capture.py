"""Pin source capture: package layout, limits, consistency retries, mail auth and digest."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from chronikwerk.archiving.capture import CapturedSource, CaptureOptions, capture_ticket
from chronikwerk.archiving.capture_errors import (
    AttachmentTooLargeError,
    InconsistentSourceError,
    RecordClassMissingError,
    TempSpaceExceededError,
)
from chronikwerk.archiving.capture_inventory import mail_auth_from_eml
from chronikwerk.failures import PermanentError, TransientError
from chronikwerk.preservation.manifest import FileRole
from chronikwerk.preservation.package import PackageTooLargeError
from tests.support.fake_capture import ARTICLES as _ARTICLES
from tests.support.fake_capture import ATTACHMENT as _ATTACHMENT
from tests.support.fake_capture import CAPTURE_OPTIONS as _OPTIONS
from tests.support.fake_capture import CLOCK_VALUE as _CLOCK_VALUE
from tests.support.fake_capture import EML as _EML
from tests.support.fake_capture import LATER as _LATER
from tests.support.fake_capture import FakeCaptureClient
from tests.support.fake_capture import fixture_ticket as _ticket


def _capture(
    tmp_path: Path,
    client: FakeCaptureClient | None = None,
    options: CaptureOptions = _OPTIONS,
    name: str = "work",
    service_login: str | None = None,
) -> CapturedSource:
    """Run capture_ticket for ticket 42 into a fresh work directory below ``tmp_path``."""
    work_dir = tmp_path / name
    work_dir.mkdir()
    return asyncio.run(
        capture_ticket(
            client or FakeCaptureClient(),
            ticket_id=42,
            work_dir=work_dir,
            options=options,
            clock=lambda: _CLOCK_VALUE,
            service_login=service_login,
        )
    )


def _files_on_disk(directory: Path) -> list[str]:
    """List every regular file below ``directory`` as sorted relative POSIX paths."""
    return sorted(
        path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()
    )


def test_capture_writes_the_package_layout_with_roles_and_media_types(tmp_path: Path) -> None:
    captured = _capture(tmp_path)
    files = {file.path: file for file in captured.builder.files()}
    expected = {
        "attachments/101/501/error.png": (FileRole.ATTACHMENT, "image/png"),
        "source/articles.json": (FileRole.SOURCE_ARTICLES, "application/json"),
        "source/articles/101.eml": (FileRole.ORIGINAL_EMAIL, "message/rfc822"),
        "source/ticket.json": (FileRole.SOURCE_TICKET, "application/json"),
    }
    assert {path: (file.role, file.media_type) for path, file in files.items()} == expected
    assert captured.build_dir == tmp_path / "work" / "attempt-1"
    assert _files_on_disk(captured.build_dir) == sorted(expected)
    assert (captured.build_dir / "source/articles/101.eml").read_bytes() == _EML
    assert (captured.build_dir / "attachments/101/501/error.png").read_bytes() == _ATTACHMENT
    assert json.loads((captured.build_dir / "source/ticket.json").read_bytes()) == _ticket()
    assert captured.record_class == "it-authorization"
    assert captured.ticket.number == "31042"
    assert [article.id for article in captured.articles] == [101, 102]
    assert captured.attempts == 1
    assert captured.collection_started == captured.collection_finished == _CLOCK_VALUE


def test_capture_reports_full_coverage_and_inventories(tmp_path: Path) -> None:
    captured = _capture(tmp_path)
    coverage = captured.coverage
    assert (coverage.articles.total, coverage.articles.included) == (2, 2)
    assert (coverage.attachments.total, coverage.attachments.included) == (1, 1)
    emails = coverage.original_emails
    assert (emails.expected, emails.included, emails.unavailable) == (1, 1, ())
    assert coverage.complete is True
    assert captured.inventory_before == captured.inventory_after
    assert captured.inventory_before == {
        "ticket_updated_at": "2026-10-20T08:15:03.000Z",
        "articles": [[101, "2026-10-20T07:58:41.000Z"], [102, "2026-10-20T08:15:02.000Z"]],
        "attachments": [[501, 29935]],
    }


def test_source_digest_is_deterministic_across_runs(tmp_path: Path) -> None:
    first = _capture(tmp_path, name="one")
    second = _capture(tmp_path, name="two")
    assert first.source_digest.startswith("sha256:")
    assert first.source_digest == second.source_digest


def test_source_digest_ignores_tags_and_updated_at(tmp_path: Path) -> None:
    baseline = _capture(tmp_path, name="base")
    noisy_articles = [{**item, "updated_at": "2026-10-30T10:00:00.000Z"} for item in _ARTICLES]
    noisy = FakeCaptureClient(
        tickets=lambda _call: _ticket(
            tags=["pdf:processing"], updated_at="2026-10-30T10:00:00.000Z", article_count=9
        ),
        articles=noisy_articles,
    )
    assert _capture(tmp_path, noisy, name="noisy").source_digest == baseline.source_digest
    retitled = FakeCaptureClient(tickets=lambda _call: _ticket(title="Other"))
    assert _capture(tmp_path, retitled, name="retitled").source_digest != baseline.source_digest


def test_source_digest_uses_the_configured_archive_field_names(tmp_path: Path) -> None:
    options = replace(_OPTIONS, archive_path_field_name="custom_path")
    baseline = _capture(tmp_path, FakeCaptureClient(), options, name="base")
    renamed = FakeCaptureClient(tickets=lambda _call: _ticket(custom_path="Elsewhere"))
    assert _capture(tmp_path, renamed, options, name="renamed").source_digest != (
        baseline.source_digest
    )


def test_source_digest_leaves_out_this_services_own_internal_notes(tmp_path: Path) -> None:
    def with_note(**changes: Any) -> FakeCaptureClient:
        note = {**_ARTICLES[1], "id": 900, "body": "Archive revision complete", **changes}
        return FakeCaptureClient(articles=[*_ARTICLES, note])

    def digest(client: FakeCaptureClient, name: str) -> str:
        return _capture(tmp_path, client, name=name, service_login="chronikwerk").source_digest

    baseline = digest(FakeCaptureClient(), "base")
    own_note = _capture(
        tmp_path, with_note(created_by="chronikwerk"), name="own", service_login="chronikwerk"
    )
    assert own_note.source_digest == baseline
    assert len(own_note.articles) == len(_ARTICLES) + 1  # still archived, only not digested
    assert digest(with_note(created_by="carol"), "agent") != baseline
    assert digest(with_note(created_by="chronikwerk", internal=False), "public") != baseline


def test_missing_original_email_is_listed_as_unavailable(tmp_path: Path) -> None:
    captured = _capture(tmp_path, FakeCaptureClient(emls={}))
    emails = captured.coverage.original_emails
    assert (emails.expected, emails.included, emails.unavailable) == (1, 0, (101,))
    assert captured.coverage.complete is True
    assert captured.mail_auth == {}
    assert "source/articles/101.eml" not in _files_on_disk(captured.build_dir)


def test_attachment_over_limit_is_permanent_and_leaves_nothing(tmp_path: Path) -> None:
    options = replace(_OPTIONS, max_attachment_bytes=1000)
    with pytest.raises(AttachmentTooLargeError) as caught:
        _capture(tmp_path, options=options)
    assert caught.value.code == "attachment_too_large"
    assert isinstance(caught.value, PermanentError)
    left = _files_on_disk(tmp_path / "work" / "attempt-1")
    assert left == ["source/articles.json", "source/articles/101.eml", "source/ticket.json"]


def test_original_email_is_bounded_by_the_attachment_limit(tmp_path: Path) -> None:
    """An email may exceed the metadata limit; only the attachment limit bounds it."""
    big_eml = _EML + b"\n" + b"x" * 8192
    small_metadata = replace(_OPTIONS, max_metadata_bytes=4096)
    fits = _capture(tmp_path, FakeCaptureClient(emls={101: big_eml}), small_metadata, name="fits")
    assert (fits.build_dir / "source/articles/101.eml").read_bytes() == big_eml

    too_small = replace(_OPTIONS, max_attachment_bytes=len(big_eml) - 1)
    with pytest.raises(AttachmentTooLargeError, match="original email of article 101") as caught:
        _capture(tmp_path, FakeCaptureClient(emls={101: big_eml}), too_small, name="over")
    assert caught.value.code == "attachment_too_large"


def test_package_over_limit_raises_and_removes_the_download(tmp_path: Path) -> None:
    options = replace(_OPTIONS, max_package_bytes=20_000)
    with pytest.raises(PackageTooLargeError):
        _capture(tmp_path, options=options)
    left = _files_on_disk(tmp_path / "work" / "attempt-1")
    assert left == ["source/articles.json", "source/articles/101.eml", "source/ticket.json"]


def test_temp_space_limit_is_transient(tmp_path: Path) -> None:
    options = replace(_OPTIONS, max_temp_bytes=10_000)
    with pytest.raises(TempSpaceExceededError) as caught:
        _capture(tmp_path, options=options)
    assert caught.value.code == "temp_space_exceeded"
    assert isinstance(caught.value, TransientError)
    assert not any(name.startswith("attachments/") for name in _files_on_disk(tmp_path / "work"))


def test_inventory_change_triggers_one_retry_then_stabilises(tmp_path: Path) -> None:
    def tickets(call: int) -> dict[str, Any]:
        """Change updated_at between the first before/after pair only."""
        return _ticket(updated_at=_LATER if call else "2026-10-20T08:15:03.000Z")

    client = FakeCaptureClient(tickets=tickets)
    captured = _capture(tmp_path, client)
    assert captured.attempts == 2
    assert captured.build_dir == tmp_path / "work" / "attempt-2"
    assert captured.inventory_before["ticket_updated_at"] == _LATER
    assert client.ticket_calls == 4
    assert (tmp_path / "work" / "attempt-1").is_dir()


def test_always_changing_source_is_inconsistent_after_all_attempts(tmp_path: Path) -> None:
    client = FakeCaptureClient(
        tickets=lambda call: _ticket(updated_at=f"2026-10-30T10:00:{call:02d}Z")
    )
    options = replace(_OPTIONS, consistency_attempts=2)
    with pytest.raises(InconsistentSourceError) as caught:
        _capture(tmp_path, client, options)
    assert caught.value.code == "inconsistent_source"
    assert client.ticket_calls == 4
    assert sorted(path.name for path in (tmp_path / "work").iterdir()) == [
        "attempt-1",
        "attempt-2",
    ]


@pytest.mark.parametrize("value", [None, "", "   ", "unknown-class", 7])
def test_missing_or_unknown_record_class_is_rejected(tmp_path: Path, value: Any) -> None:
    client = FakeCaptureClient(tickets=lambda _call: _ticket(record_class=value))
    with pytest.raises(RecordClassMissingError) as caught:
        _capture(tmp_path, client)
    assert caught.value.code == "record_class_missing"
    assert _files_on_disk(tmp_path / "work") == []


def test_record_class_is_read_from_the_configured_field(tmp_path: Path) -> None:
    options = replace(_OPTIONS, record_class_field_name="retention_class")
    client = FakeCaptureClient(tickets=lambda _call: _ticket(retention_class=" general "))
    assert _capture(tmp_path, client, options).record_class == "general"


def test_mail_auth_is_extracted_from_the_original_email(tmp_path: Path) -> None:
    captured = _capture(tmp_path)
    auth = captured.mail_auth[101]
    assert len(auth["authentication_results"]) == 1
    assert "dkim=pass header.d=example.com" in auth["authentication_results"][0]
    assert "spf=pass smtp.mailfrom=example.com" in auth["authentication_results"][0]
    assert auth["received_spf"] == []
    assert auth["dkim_signature_domains"] == ["example.com"]


def test_mail_auth_records_every_header_and_signature_domain() -> None:
    eml = (
        b"Authentication-Results: mx1.example.com; spf=pass\r\n"
        b"Authentication-Results: mx2.example.com; dkim=fail\r\n"
        b"Received-SPF: pass (mx1.example.com: domain of a@example.org) client-ip=192.0.2.1\r\n"
        b"DKIM-Signature: v=1; a=rsa-sha256; d=example.org; s=s1; b=AAAA\r\n"
        b"DKIM-Signature: v=1; a=rsa-sha256;\r\n d = relay.example.net ; s=s2; b=BBBB\r\n"
        b"DKIM-Signature: v=1; a=rsa-sha256; s=s3; b=CCCC\r\n"
        b"Subject: hi\r\n\r\nbody\r\n"
    )
    auth = mail_auth_from_eml(eml)
    assert auth["order"] == "as-in-message, topmost first"
    assert auth["verified"] is False
    assert "not validated" in auth["provenance"]
    assert auth["authentication_results"] == [
        "mx1.example.com; spf=pass",
        "mx2.example.com; dkim=fail",
    ]
    assert auth["received_spf"] == [
        "pass (mx1.example.com: domain of a@example.org) client-ip=192.0.2.1"
    ]
    assert auth["dkim_signature_domains"] == ["example.org", "relay.example.net", None]


def test_string_and_integer_attachment_sizes_give_the_same_inventory(tmp_path: Path) -> None:
    articles = json.loads(json.dumps(_ARTICLES))
    articles[0]["attachments"][0]["size"] = 29935
    numeric = _capture(tmp_path, FakeCaptureClient(articles=articles), name="numeric")
    textual = _capture(tmp_path, name="textual")
    assert numeric.inventory_before["attachments"] == [[501, 29935]]
    assert textual.inventory_before["attachments"] == numeric.inventory_before["attachments"]


def test_attachment_without_media_type_preference_is_octet_stream(tmp_path: Path) -> None:
    articles = json.loads(json.dumps(_ARTICLES))
    articles[0]["attachments"][0]["preferences"] = {}
    articles[0]["attachments"][0]["filename"] = "../Bericht März.pdf"
    captured = _capture(tmp_path, FakeCaptureClient(articles=articles))
    attachment = next(f for f in captured.builder.files() if f.role is FileRole.ATTACHMENT)
    assert attachment.path == "attachments/101/501/_.._Bericht_Marz.pdf"
    assert attachment.media_type == "application/octet-stream"


@pytest.mark.parametrize(
    ("preferences", "expected"),
    [
        ({"Content-Type": "Text/Plain; charset=UTF-8"}, "text/plain"),
        ({"Mime-Type": "application/vnd.ms-excel.sheet.macroEnabled.12"}, None),
        ({"Content-Type": "image/svg+xml"}, "image/svg+xml"),
        ({"Content-Type": "text/html\r\nX-Injected: 1"}, "application/octet-stream"),
        ({"Content-Type": "no-slash"}, "application/octet-stream"),
        ({"Content-Type": "text/plain/extra"}, "application/octet-stream"),
        ({"Content-Type": "-bad/start"}, "application/octet-stream"),
        ({"Content-Type": "text/" + "a" * 128}, "application/octet-stream"),
        ({"Content-Type": "ima ge/png"}, "application/octet-stream"),
    ],
)
def test_attachment_media_type_must_be_an_rfc_6838_name(
    tmp_path: Path, preferences: dict[str, str], expected: str | None
) -> None:
    articles = json.loads(json.dumps(_ARTICLES))
    articles[0]["attachments"][0]["preferences"] = preferences
    captured = _capture(tmp_path, FakeCaptureClient(articles=articles))
    attachment = next(f for f in captured.builder.files() if f.role is FileRole.ATTACHMENT)
    lowered = next(iter(preferences.values())).lower()
    assert attachment.media_type == (lowered if expected is None else expected)


def test_capture_options_reject_non_positive_limits() -> None:
    with pytest.raises(ValueError, match="consistency_attempts"):
        replace(_OPTIONS, consistency_attempts=0)
    with pytest.raises(ValueError, match="max_temp_bytes"):
        replace(_OPTIONS, max_temp_bytes=0)
