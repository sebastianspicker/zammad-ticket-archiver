"""Sanitize ticket article HTML before it reaches the document template engine."""

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
        self._skip_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Emit only allowed start tags and attributes while bounding nesting depth."""
        tag = tag.lower()
        if tag in _DROP_WITH_CONTENT:
            self._skip_stack.append(tag)
            return
        if self._skip_stack or tag not in _ALLOWED_TAGS:
            return
        cleaned = self._clean_attrs(tag, attrs)
        attr_text = "".join(f' {key}="{escape(value, quote=True)}"' for key, value in cleaned)
        if tag in _VOID_TAGS:
            self._out.append(f"<{tag}{attr_text} />")
            return
        if len(self._open) >= 50:
            return
        self._out.append(f"<{tag}{attr_text}>")
        self._open.append(_OpenTag(tag))

    def _clean_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> list[tuple[str, str]]:
        allowed = _ALLOWED_ATTRS.get(tag, frozenset())
        cleaned: list[tuple[str, str]] = []
        for key, value in attrs:
            candidate = _allowed_attr(key, value, allowed=allowed)
            if candidate is None:
                continue
            normalized, value = candidate
            if tag == "a" and normalized == "href":
                value = _sanitize_href(value)
                if value is None:
                    continue
            cleaned.append((normalized, value))
        return cleaned

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Preserve permitted self-closing markup during sanitization."""
        self.handle_starttag(tag, attrs)

    def _discard_skipped_tag(self, tag: str) -> None:
        """Pop the skipped tag and its nested descendants when it closes."""
        for index in range(len(self._skip_stack) - 1, -1, -1):
            if self._skip_stack[index] == tag:
                del self._skip_stack[index:]
                return

    def _close_open_tag(self, tag: str) -> None:
        """Close a matching open tag and any malformed nested descendants."""
        for index in range(len(self._open) - 1, -1, -1):
            if self._open[index].name == tag:
                for item in reversed(self._open[index:]):
                    self._out.append(f"</{item.name}>")
                del self._open[index:]
                return

    def handle_endtag(self, tag: str) -> None:
        """Close allowed elements without reproducing skipped or malformed nesting."""
        tag = tag.lower()
        if tag in _DROP_WITH_CONTENT:
            self._discard_skipped_tag(tag)
            return
        if self._skip_stack or tag in _VOID_TAGS:
            return
        self._close_open_tag(tag)

    def handle_data(self, data: str) -> None:
        """Append textual content while preserving meaningful spacing."""
        if not self._skip_stack and data:
            self._out.append(escape(data))

    def close(self) -> None:
        """Finish parsing and close any allowed elements left open by malformed input."""
        super().close()
        while self._open:
            self._out.append(f"</{self._open.pop().name}>")

    def sanitized_html(self) -> str:
        """Return sanitized HTML suitable for embedding in a trusted template."""
        return "".join(self._out).strip()


def sanitize_html_fragment(html: str) -> str:
    """Return a dependency-free, allowlisted HTML fragment for PDF rendering."""
    if not isinstance(html, str) or not html:
        return ""
    try:
        parser = _AllowlistHTMLSanitizer()
        parser.feed(html)
        parser.close()
        return parser.sanitized_html()
    except ValueError:
        return ""


def _allowed_attr(
    key: str,
    value: str | None,
    *,
    allowed: frozenset[str],
) -> tuple[str, str] | None:
    if value is None:
        return None
    normalized = key.lower().strip()
    if not normalized:
        return None
    if normalized.startswith("on") or normalized == "style":
        return None
    if normalized not in allowed:
        return None
    return normalized, value
