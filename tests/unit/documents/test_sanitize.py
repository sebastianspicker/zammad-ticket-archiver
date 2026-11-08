"""Pin the allowlist behavior of ``sanitize_html_fragment`` with exact outputs."""

from __future__ import annotations

import pytest

from chronikwerk.documents.sanitize import sanitize_html_fragment


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ("<p>a<script>alert(1)</script>b</p>", "<p>ab</p>"),
        ("<style>p{}</style>x", "x"),
        ("<iframe src=x>inner</iframe>y", "y"),
        ("<object><p>in</p></object>z", "z"),
        ("<script><p>x</p></script>after", "after"),
        ("<form><input><button>go</button></form>t", "t"),
        ("<img src=x onerror=alert(1)>t", "t"),
        ('<meta charset="utf-8"><p>Hello</p>', "<p>Hello</p>"),
        ("<p>a</p><link rel=x><base href=y><p>b</p>", "<p>a</p><p>b</p>"),
        ("<p>a</p><input><p>b</p>", "<p>a</p><p>b</p>"),
    ],
)
def test_dangerous_elements_are_removed_with_their_content(markup: str, expected: str) -> None:
    """Script, style, frame, object and form elements disappear together with their content."""
    assert sanitize_html_fragment(markup) == expected


def test_nested_script_ends_at_the_first_closing_tag_and_leaks_only_escaped_text() -> None:
    """Raw-text script handling ends at the first close tag; leftovers are plain text."""
    assert sanitize_html_fragment("<script>a<script>b</script>c</script>d") == "cd"
    assert sanitize_html_fragment("<p <script>alert(1)</script>>x</p>") == "<p>alert(1)&gt;x</p>"


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ('<p onclick="x()" onMouseOver=y>t</p>', "<p>t</p>"),
        ('<p style="color:red" class="c" id="i">t</p>', "<p>t</p>"),
        (
            '<table><tr><td colspan="2" style="x" onclick="y">c</td></tr></table>',
            '<table><tr><td colspan="2">c</td></tr></table>',
        ),
        (
            '<a href="https://e.x/" onclick="y" target="_blank">x</a>',
            '<a href="https://e.x/">x</a>',
        ),
    ],
)
def test_event_style_and_unlisted_attributes_are_removed(markup: str, expected: str) -> None:
    """Only attributes on the per-tag allowlist survive; on* and style never do."""
    assert sanitize_html_fragment(markup) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", "1"), ("02", "2"), ("32", "32")],
)
def test_bounded_table_spans_are_canonicalized(value: str, expected: str) -> None:
    """Ordinary table spans survive as bounded canonical decimal values."""
    markup = f'<table><tr><th rowspan="{value}">h</th><td colspan="{value}">c</td></tr></table>'

    assert sanitize_html_fragment(markup) == (
        f'<table><tr><th rowspan="{expected}">h</th><td colspan="{expected}">c</td></tr></table>'
    )


@pytest.mark.parametrize(
    "value",
    ["0", "33", "9" * 200, "+2", "-2", " 2", "2 ", "2x", "\u0662"],
)
def test_unsafe_table_spans_are_removed(value: str) -> None:
    """Malformed, non-positive, and oversized spans never reach the renderer."""
    markup = f'<table><tr><td colspan="{value}" rowspan="{value}">c</td></tr></table>'

    assert sanitize_html_fragment(markup) == "<table><tr><td>c</td></tr></table>"


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "vbscript:x",
        "data:text/html,hi",
        "tel:1",
        "ftp://x",
        "//evil.com",
        " JaVaScRiPt:alert(1)",
        "java\tscript:alert(1)",
        "jav&#x61;script:alert(1)",
        "&#106;avascript:alert(1)",
        "\x00javascript:x",
        "",
    ],
)
def test_unsafe_or_disallowed_href_is_dropped_but_the_link_text_stays(href: str) -> None:
    """Disallowed schemes, encoded schemes and protocol-relative links lose their href."""
    assert sanitize_html_fragment(f'<a href="{href}">x</a>') == "<a>x</a>"


@pytest.mark.parametrize(
    ("href", "expected_href"),
    [
        ("https://e.x/a?b=1&c=2", "https://e.x/a?b=1&amp;c=2"),
        ("http://e.x", "http://e.x"),
        ("mailto:a@b.c", "mailto:a@b.c"),
        ("/relative/path", "/relative/path"),
        ("#anchor", "#anchor"),
        ("HTTPS://E.X", "HTTPS://E.X"),
    ],
)
def test_allowed_href_schemes_are_kept_and_attribute_escaped(href: str, expected_href: str) -> None:
    """Only http, https, mailto and scheme-less hrefs are allowed; values are escaped."""
    assert sanitize_html_fragment(f'<a href="{href}">x</a>') == f'<a href="{expected_href}">x</a>'


def test_allowed_tags_and_attributes_are_preserved_in_normalized_form() -> None:
    """Allowed formatting, list, table and void tags are emitted in lower case."""
    markup = (
        '<H1>T</H1><P CLASS="x"><STRONG>B</STRONG> <em>i</em> <code>c</code></p>'
        '<ul><li>a</li></ul><a title="a&quot;b" href="/x">l</a><br><hr><br/>'
    )
    assert sanitize_html_fragment(markup) == (
        "<h1>T</h1><p><strong>B</strong> <em>i</em> <code>c</code></p>"
        '<ul><li>a</li></ul><a title="a&quot;b" href="/x">l</a><br /><hr /><br />'
    )


def test_unlisted_tags_are_unwrapped_and_keep_their_children() -> None:
    """Unknown or unsafe-by-default containers lose their tag but keep allowed children."""
    assert sanitize_html_fragment("<svg><p>x</p></svg>") == "<p>x</p>"
    assert sanitize_html_fragment("<font><u>x</u></font>") == "<u>x</u>"


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ("<p><b>unclosed", "<p><b>unclosed</b></p>"),
        ("<p><b>x</p>y", "<p><b>x</b></p>y"),
        ("<div><span>a</div>b", "<div><span>a</span></div>b"),
        ("<b><i>x</b></i>", "<b><i>x</i></b>"),
        ("</p>stray", "stray"),
        ("<!-- c -->t", "t"),
    ],
)
def test_malformed_markup_is_rebalanced(markup: str, expected: str) -> None:
    """Unclosed tags are closed, misnested tags are repaired and stray end tags are dropped."""
    assert sanitize_html_fragment(markup) == expected


def test_text_is_escaped_and_entity_encoded_markup_stays_inert_text() -> None:
    """Bare angle brackets and ampersands are escaped; encoded tags are never re-parsed."""
    assert sanitize_html_fragment("<p>1 < 2 & 3 > 2</p>") == "<p>1 &lt; 2 &amp; 3 &gt; 2</p>"
    assert (
        sanitize_html_fragment("&lt;script&gt;alert(1)&lt;/script&gt;")
        == "&lt;script&gt;alert(1)&lt;/script&gt;"
    )
    assert sanitize_html_fragment("<p>ä&nbsp;x</p>") == "<p>ä x</p>"


def test_nesting_depth_is_capped_at_fifty_open_elements() -> None:
    """Elements beyond the fiftieth nesting level are dropped while their text survives."""
    result = sanitize_html_fragment("<div>" * 60 + "x")
    assert result.count("<div>") == 50
    assert result.count("</div>") == 50
    assert "x" in result


@pytest.mark.parametrize("value", ["", "   ", None])
def test_empty_or_non_string_input_yields_an_empty_fragment(value: object) -> None:
    """Empty, whitespace-only and non-string input sanitize to an empty string."""
    assert sanitize_html_fragment(value) == ""  # type: ignore[arg-type]
