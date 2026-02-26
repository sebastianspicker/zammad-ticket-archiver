from __future__ import annotations

from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from typing import Final
from urllib.parse import urlparse

_ALLOWED_TAGS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "b",
        "blockquote",
        "br",
        "code",
        "div",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "li",
        "ol",
        "p",
        "pre",
        "span",
        "strong",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
)

_DROP_WITH_CONTENT: Final[frozenset[str]] = frozenset(
    {
        "script",
        "style",
        "iframe",
        "object",
        "embed",
        "link",
        "meta",
        "base",
        "form",
        "input",
        "button",
        "textarea",
        "select",
        "option",
    }
)

_VOID_TAGS: Final[frozenset[str]] = frozenset({"br", "hr"})

_ALLOWED_ATTRS: Final[dict[str, frozenset[str]]] = {
    "a": frozenset({"href", "title"}),
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan"}),
}

_ALLOWED_HREF_SCHEMES: Final[frozenset[str]] = frozenset({"", "http", "https", "mailto"})


def _sanitize_href(raw: str) -> str | None:
    href = raw.strip()
    if not href or "\x00" in href:
        return None

    parsed = urlparse(href)
    scheme = (parsed.scheme or "").lower()

    # Disallow scheme-relative URLs like //example.com (netloc present, no scheme).
    if not scheme and parsed.netloc:
        return None

    if scheme not in _ALLOWED_HREF_SCHEMES:
        return None

    return href


@dataclass
class _OpenTag:
    name: str


class _AllowlistHTMLSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._open: list[_OpenTag] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._mark_skip_depth(tag):
            return
        if self._skip_depth:
            return

        if not self._is_allowed_tag(tag):
            return

        cleaned = self._clean_attrs(tag, attrs)
        attr_text = "".join(f' {k}="{escape(v, quote=True)}"' for k, v in cleaned)
        if tag in _VOID_TAGS:
            self._out.append(f"<{tag}{attr_text} />")
            return

        self._out.append(f"<{tag}{attr_text}>")
        self._open.append(_OpenTag(tag))

    def _mark_skip_depth(self, tag: str) -> bool:
        if tag in _DROP_WITH_CONTENT:
            self._skip_depth += 1
            return True
        return False

    def _is_allowed_tag(self, tag: str) -> bool:
        # Bug #P2-7: Limit nesting depth to prevent resource exhaustion.
        return tag in _ALLOWED_TAGS and len(self._open) < 50

    def _clean_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> list[tuple[str, str]]:
        allowed_attrs = _ALLOWED_ATTRS.get(tag, frozenset())
        cleaned: list[tuple[str, str]] = []
        for key, value in attrs:
            normalized = self._normalized_attr_key(key)
            if normalized is None or value is None:
                continue
            if normalized not in allowed_attrs:
                continue
            sanitized = self._sanitize_attr_value(tag, normalized, value)
            if sanitized is None:
                continue
            cleaned.append((normalized, sanitized))
        return cleaned

    @staticmethod
    def _normalized_attr_key(key: str | None) -> str | None:
        if not key:
            return None
        key_norm = key.lower().strip()
        if not key_norm or key_norm.startswith("on") or key_norm == "style":
            return None
        return key_norm

    @staticmethod
    def _sanitize_attr_value(tag: str, key: str, value: str) -> str | None:
        if tag == "a" and key == "href":
            return _sanitize_href(value)
        return value

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Normalize <br/> style tags; route through the same allowlist logic.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _DROP_WITH_CONTENT and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in _VOID_TAGS:
            return

        if not self._open:
            return
        if self._open[-1].name != tag:
            return
        self._open.pop()
        self._out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if data:
            self._out.append(escape(data))

    def close(self) -> None:
        super().close()
        # Close any still-open tags to keep output well-formed.
        while self._open:
            tag = self._open.pop().name
            self._out.append(f"</{tag}>")

    def sanitized_html(self) -> str:
        return "".join(self._out).strip()


def sanitize_html_fragment(html: str) -> str:
    """
    Sanitize an HTML fragment using a strict allowlist.

    Security goals (minimum per docs/05-pdf-rendering.md):
      - Drop <script>/<style> and similar active content.
      - Remove event-handler attributes (onclick, ...).
      - Neutralize dangerous URL schemes (javascript:, data:, file:, ...).

    This is intended for rendering ticket content into PDFs (print output), not for general-purpose
    HTML.
    """
    if not isinstance(html, str) or not html:
        return ""

    try:
        parser = _AllowlistHTMLSanitizer()
        parser.feed(html)
        parser.close()
        return parser.sanitized_html()
    except Exception:
        # Fail closed: return empty so callers can fall back to rendering body_text.
        return ""
