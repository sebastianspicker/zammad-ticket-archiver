from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import (
    Environment,
    FileSystemLoader,
    PackageLoader,
    pass_context,
    select_autoescape,
)
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


@lru_cache(maxsize=32)
def _env_for(template_name: str, templates_root: Path | None = None) -> Environment:
    template_name = validate_template_name(template_name)

    loader = _loader_for(template_name, templates_root=templates_root)

    env = Environment(
        loader=loader,
        autoescape=select_autoescape(["html", "xml"]),
    )
    _register_filters(env)
    return env


def _loader_for(template_name: str, templates_root: Path | None) -> BaseLoader:
    if templates_root is None:
        return PackageLoader("zammad_pdf_archiver", "templates")

    if not templates_root.exists() or not templates_root.is_dir():
        raise FileNotFoundError(f"Template root folder not found: {templates_root}")
    template_dir = templates_root / template_name
    if not template_dir.exists() or not template_dir.is_dir():
        raise FileNotFoundError(f"Template folder not found: {template_dir}")
    return FileSystemLoader(str(templates_root))


def _register_filters(env: Environment) -> None:
    def format_dt(value: Any, tz_name: str = "UTC") -> str:
        return _format_datetime(value, tz_name=tz_name, fmt="%Y-%m-%d %H:%M")

    @pass_context
    def format_dt_local(context: Any, value: Any, fmt: str = "%Y-%m-%d %H:%M") -> str:
        tz_name = context.get("pdf_timezone", "UTC")
        return _format_datetime(value, tz_name=tz_name, fmt=fmt)

    env.filters["format_dt"] = format_dt
    env.filters["format_dt_local"] = format_dt_local


def _format_datetime(value: Any, *, tz_name: str, fmt: str) -> str:
    if not value or not hasattr(value, "strftime"):
        return str(value) if value is not None else "—"
    try:
        target_tz = ZoneInfo(tz_name)
        localized = value.astimezone(target_tz)
        return localized.strftime(fmt)
    except Exception:
        return value.strftime(fmt) if hasattr(value, "strftime") else str(value)


def render_html(
    snapshot: Snapshot,
    template_name: str,
    *,
    locale: str = "de_DE",
    timezone: str = "Europe/Berlin",
    templates_root: Path | None = None,
) -> str:
    """
    Render a Snapshot to HTML using templates/<template_name>/ticket.html.

    Jinja context is restricted to a minimal whitelist (Bug #39): only snapshot,
    ticket, and articles are passed; no config, request, or full object graph.
    """
    env = _env_for(template_name, templates_root=templates_root)
    template = env.get_template(f"{template_name}/{_TEMPLATE_FILE}")
    return template.render(
        snapshot=snapshot,
        ticket=snapshot.ticket,
        articles=snapshot.articles,
        pdf_locale=locale,
        pdf_timezone=timezone,
    )
