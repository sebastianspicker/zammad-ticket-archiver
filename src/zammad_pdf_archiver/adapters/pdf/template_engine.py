from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, PackageLoader, select_autoescape
from jinja2.loaders import BaseLoader

from zammad_pdf_archiver.domain.snapshot_models import Snapshot

_TEMPLATE_FILE = "ticket.html"


def _templates_root_override() -> Path | None:
    if not (value := os.environ.get("TEMPLATES_ROOT")):
        return None
    path = Path(value).expanduser()
    return path


@lru_cache(maxsize=32)
def _env_for(template_name: str) -> Environment:
    if not isinstance(template_name, str) or not template_name.strip():
        raise ValueError("template_name must be a non-empty string")

    template_name = template_name.strip()

    loader: BaseLoader
    if (templates_root := _templates_root_override()) is not None:
        template_dir = templates_root / template_name
        if not template_dir.exists() or not template_dir.is_dir():
            raise FileNotFoundError(f"Template folder not found: {template_dir}")
        loader = FileSystemLoader(str(template_dir))
    else:
        loader = PackageLoader("zammad_pdf_archiver", f"templates/{template_name}")

    return Environment(
        loader=loader,
        autoescape=select_autoescape(["html", "xml"]),
    )


def render_html(snapshot: Snapshot, template_name: str) -> str:
    """
    Render a Snapshot to HTML using templates/<template_name>/ticket.html.

    Jinja context contract:
      - snapshot: Snapshot
      - ticket: snapshot.ticket
      - articles: snapshot.articles
    """
    env = _env_for(template_name)
    template = env.get_template(_TEMPLATE_FILE)
    return template.render(snapshot=snapshot, ticket=snapshot.ticket, articles=snapshot.articles)
