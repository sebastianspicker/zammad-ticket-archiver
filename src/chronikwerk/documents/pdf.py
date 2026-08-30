"""Render archive snapshots as deterministic, accessible PDFs through WeasyPrint."""

# pylint: disable=import-outside-toplevel
from __future__ import annotations

import hashlib
import threading
import warnings
from collections.abc import Generator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from chronikwerk.documents.models import Snapshot
from chronikwerk.documents.templates import (
    DEFAULT_TEMPLATE_NAME,
    render_html,
)
from chronikwerk.documents.url_fetcher import _safe_url_fetcher
from chronikwerk.failures import PermanentError
from chronikwerk.operations.async_work import run_sync_cancellation_safe

_TEMPLATE_STYLES_MAIN = "styles.css"
_WEASYPRINT_RENDER_LOCK = threading.Lock()


@contextmanager
def _template_folder_path() -> Generator[Path]:
    traversable = resources.files("chronikwerk.documents").joinpath(
        "templates", DEFAULT_TEMPLATE_NAME
    )
    with resources.as_file(traversable) as path:
        yield path


def _css_file_paths(template_folder: Path) -> tuple[Path, ...]:
    main = template_folder / _TEMPLATE_STYLES_MAIN
    if not main.is_file():
        raise FileNotFoundError(f"Template CSS not found: {main}")
    return (main,)


def _validate_article_limit(snapshot: Snapshot, max_articles: int) -> None:
    if 0 < max_articles < len(snapshot.articles):
        raise PermanentError(
            f"too many articles: ticket has {len(snapshot.articles)} articles; "
            f"maximum allowed is {max_articles}"
        )


def _pdf_identifier(html: str, css_paths: tuple[Path, ...]) -> bytes:
    css_bytes = b"\0".join(path.read_bytes() for path in css_paths)
    return hashlib.sha256(html.encode("utf-8") + b"\0" + css_bytes).digest()[:16]


def _write_pdf_unlocked(html: str, *, template_folder: Path, css_paths: tuple[Path, ...]) -> bytes:
    from weasyprint import CSS, HTML

    stylesheets = [CSS(filename=str(path)) for path in css_paths]
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="PDF objects don.t version identifier initialization anymore.*",
            category=DeprecationWarning,
        )
        html_doc = HTML(
            string=html,
            base_url=str(template_folder),
            url_fetcher=_safe_url_fetcher(template_folder),
        )
        return html_doc.write_pdf(
            stylesheets=stylesheets,
            pdf_identifier=_pdf_identifier(html, css_paths),
            pdf_variant="pdf/ua-1",
        )


def _write_pdf(html: str, *, template_folder: Path, css_paths: tuple[Path, ...]) -> bytes:
    # FontConfig/Pango-backed rendering can crash the interpreter when two
    # worker threads enter WeasyPrint concurrently. Keep the lock off the event
    # loop: this function only runs inside ``run_sync_cancellation_safe``.
    with _WEASYPRINT_RENDER_LOCK:
        return _write_pdf_unlocked(html, template_folder=template_folder, css_paths=css_paths)


async def render_pdf(
    snapshot: Snapshot,
    *,
    max_articles: int = 250,
    locale: str = "de-DE",
    timezone: str = "Europe/Berlin",
) -> bytes:
    """Render one validated snapshot without blocking the ASGI event loop."""
    _validate_article_limit(snapshot, max_articles)
    return await run_sync_cancellation_safe(
        _render_pdf_sync,
        snapshot,
        locale=locale,
        timezone=timezone,
    )


def _render_pdf_sync(
    snapshot: Snapshot,
    *,
    locale: str,
    timezone: str,
) -> bytes:
    """Render inside one worker thread, keeping template resources alive there."""
    with _template_folder_path() as template_folder:
        html = render_html(
            snapshot,
            locale=locale,
            timezone=timezone,
        )
        css_paths = _css_file_paths(template_folder)
        return _write_pdf(html, template_folder=template_folder, css_paths=css_paths)
