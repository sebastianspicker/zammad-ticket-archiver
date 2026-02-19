from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, PackageLoader, select_autoescape
from jinja2.loaders import BaseLoader

from zammad_pdf_archiver.domain.snapshot_models import Snapshot

_TEMPLATE_FILE = "ticket.html"

ALLOWED_TEMPLATE_NAMES: frozenset[str] = frozenset({"default", "minimal", "compact"})


def validate_template_name(template_name: str) -> str:
    """
    Validate and normalize template name. Raises ValueError if empty, contains path
    separators/traversal, or is not in allowlist (Bug #38). Returns stripped name.
    """
    if not isinstance(template_name, str):
        raise ValueError("template_name must be a string")
    name = template_name.strip()
    if not name:
        raise ValueError("template_name must be a non-empty string")
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("template_name must not contain path separators or '..'")
    if name not in ALLOWED_TEMPLATE_NAMES:
        raise ValueError(
            f"template_name must be one of {sorted(ALLOWED_TEMPLATE_NAMES)}, got {name!r}"
        )
    return name


def _templates_root_override() -> Path | None:
    if not (value := os.environ.get("TEMPLATES_ROOT")):
        return None
    path = Path(value).expanduser()
    return path


@lru_cache(maxsize=32)
def _env_for(template_name: str) -> Environment:
    template_name = validate_template_name(template_name)

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

    Jinja context is restricted to a minimal whitelist (Bug #39): only snapshot,
    ticket, and articles are passed; no config, request, or full object graph.
    """
    env = _env_for(template_name)
    template = env.get_template(_TEMPLATE_FILE)
    return template.render(snapshot=snapshot, ticket=snapshot.ticket, articles=snapshot.articles)
