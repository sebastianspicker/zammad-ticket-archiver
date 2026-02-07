from __future__ import annotations

from zammad_pdf_archiver.domain.html_sanitize import sanitize_html_fragment


def test_sanitize_html_fragment_drops_scripts_and_event_handlers() -> None:
    raw = (
        '<p onclick="alert(1)">Hello '
        "<script>alert(1)</script>"
        '<a href="javascript:alert(1)">bad</a> '
        '<a href="https://example.com/path">ok</a> '
        '<img src="https://evil.example/img.png" />'
        "</p>"
    )

    out = sanitize_html_fragment(raw)
    assert "<script" not in out
    assert "onclick" not in out
    assert "javascript:" not in out
    assert "<img" not in out
    assert 'href="https://example.com/path"' in out
    assert "Hello" in out


def test_sanitize_html_fragment_strips_unknown_tags_but_keeps_text() -> None:
    raw = "<p>Hello <custom>World</custom></p>"
    out = sanitize_html_fragment(raw)
    assert "<custom" not in out
    assert "Hello" in out
    assert "World" in out


def test_sanitize_html_fragment_rejects_scheme_relative_urls() -> None:
    raw = '<p><a href="//example.com">x</a></p>'
    out = sanitize_html_fragment(raw)
    assert "href=" not in out

