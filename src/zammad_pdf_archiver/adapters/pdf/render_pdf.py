from __future__ import annotations

import hashlib
import warnings
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from zammad_pdf_archiver.adapters.pdf.template_engine import render_html, validate_template_name
from zammad_pdf_archiver.adapters.pdf.url_fetcher import _safe_url_fetcher
from zammad_pdf_archiver.domain.errors import PermanentError
from zammad_pdf_archiver.domain.snapshot_models import Snapshot

_TEMPLATE_STYLES_MAIN = "styles.css"


@contextmanager
def _template_folder_path(template_name: str, templates_root: Path | None = None):
    template_name = validate_template_name(template_name)

    if templates_root is not None:
        yield templates_root.expanduser() / template_name
        return

    traversable = resources.files("zammad_pdf_archiver").joinpath("templates", template_name)
    with resources.as_file(traversable) as path:
        yield path


def _css_file_paths(template_folder: Path) -> list[Path]:
    if not template_folder.exists() or not template_folder.is_dir():
        raise FileNotFoundError(f"Template folder not found: {template_folder}")

    files: list[Path] = []
    main = template_folder / _TEMPLATE_STYLES_MAIN
    if main.exists() and main.is_file():
        files.append(main)

    # Add any additional CSS files in deterministic order.
    for path in sorted(template_folder.glob("*.css")):
        if path.name == _TEMPLATE_STYLES_MAIN:
            continue
        if path.is_file():
            files.append(path)

    css_dir = template_folder / "css"
    if css_dir.exists() and css_dir.is_dir():
        for path in sorted(css_dir.rglob("*.css")):
            if path.is_file():
                files.append(path)

    if not files:
        raise FileNotFoundError(f"No CSS files found in template folder: {template_folder}")
    return files


def render_pdf(
    snapshot: Snapshot,
    template_name: str,
    *,
    max_articles: int = 250,
    locale: str = "de_DE",
    timezone: str = "Europe/Berlin",
    templates_root: Path | None = None,
) -> bytes:
    """
    Render a Snapshot to PDF bytes using:
      - Jinja2 templates/<template_name>/ticket.html
      - WeasyPrint HTML -> PDF
      - CSS loaded from the template folder
    """
    if max_articles < 0:
        raise ValueError("max_articles must be >= 0")
    # 0 means "disabled/unlimited" (documented in ops runbook).
    if max_articles > 0 and len(snapshot.articles) > max_articles:
        raise PermanentError(
            f"snapshot has too many articles ({len(snapshot.articles)} > {max_articles})"
        )

    with _template_folder_path(template_name, templates_root=templates_root) as template_folder:
        html = render_html(snapshot, template_name, locale=locale, timezone=timezone)

        css_paths = _css_file_paths(template_folder)
        css_bytes = b"".join(p.read_bytes() for p in css_paths)
        pdf_identifier = hashlib.sha256(html.encode("utf-8") + b"\0" + css_bytes).digest()[:16]

        # Import lazily so the rest of the codebase can be imported without the
        # WeasyPrint native dependencies.
        from weasyprint import CSS, HTML  # type: ignore[import-untyped]

        stylesheets = [CSS(filename=str(path)) for path in css_paths]

        # Temporary compatibility shim for WeasyPrint/pydyf version skew:
        # pydyf emits a deprecation warning from internals we don't control.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=(
                    "PDF objects don’t take version or identifier during initialization anymore.*"
                ),
                category=DeprecationWarning,
            )
            html_doc = HTML(
                string=html,
                base_url=str(template_folder),
                url_fetcher=_safe_url_fetcher(template_folder),
            )
            return html_doc.write_pdf(
                stylesheets=stylesheets,
                pdf_identifier=pdf_identifier,
            )
