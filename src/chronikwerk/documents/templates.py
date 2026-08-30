"""Build localized HTML from archive snapshots and packaged Jinja templates."""

from __future__ import annotations

from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jinja2 import Environment, PackageLoader, pass_context, select_autoescape

from chronikwerk.documents.models import Snapshot
from chronikwerk.i18n import normalize_locale, plural_key, translate

DEFAULT_TEMPLATE_NAME = "default"
_TEMPLATE_FILE = "ticket.html"
_TEMPLATE_PATH = f"{DEFAULT_TEMPLATE_NAME}/{_TEMPLATE_FILE}"


@lru_cache(maxsize=1)
def _env_for() -> Environment:
    env = Environment(
        loader=PackageLoader("chronikwerk.documents", "templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )
    _register_filters(env)
    return env


def _register_filters(env: Environment) -> None:
    @pass_context
    def datetime_filter(ctx: dict[str, Any], value: Any, fmt: str = "%d.%m.%Y %H:%M") -> str:
        """Format a datetime for the configured ticket locale and timezone."""
        return _format_datetime(
            value,
            fmt=fmt,
            timezone=str(ctx.get("pdf_timezone") or "Europe/Berlin"),
            locale=str(ctx.get("pdf_locale") or "de-DE"),
        )

    env.filters["format_dt_local"] = datetime_filter

    @pass_context
    def file_size_filter(ctx: dict[str, Any], value: Any) -> str:
        """Format byte counts consistently for PDF template output."""
        return _format_file_size(value, locale=str(ctx.get("pdf_locale") or "de-DE"))

    env.filters["format_file_size"] = file_size_filter


def _format_datetime(
    value: Any,
    *,
    fmt: str,
    timezone: str,
    locale: str = "de-DE",
) -> str:
    if value is None:
        return ""
    try:
        target_tz = ZoneInfo(timezone)
        localized = value.astimezone(target_tz)
        locale_fmt = "%d.%m.%Y %H:%M" if normalize_locale(locale) == "de-DE" else "%d/%m/%Y %H:%M"
        return localized.strftime(locale_fmt if fmt == "%d.%m.%Y %H:%M" else fmt)
    except AttributeError, TypeError, ValueError, ZoneInfoNotFoundError:
        return value.strftime(fmt) if hasattr(value, "strftime") else str(value)


def _format_file_size(value: Any, *, locale: str) -> str:
    try:
        size = max(0, int(value))
    except TypeError, ValueError:
        return ""
    units = ("B", "kB", "MB", "GB")
    amount = float(size)
    unit = units[0]
    for unit in units:
        if amount < 1000 or unit == units[-1]:
            break
        amount /= 1000
    if unit == "B":
        return f"{size} B"
    formatted = f"{amount:.1f}"
    if normalize_locale(locale) == "de-DE":
        formatted = formatted.replace(".", ",")
    return f"{formatted} {unit}"


def render_html(
    snapshot: Snapshot,
    *,
    locale: str = "de-DE",
    timezone: str = "Europe/Berlin",
) -> str:
    """Render a localized ticket snapshot with the selected packaged template."""
    template = _env_for().get_template(_TEMPLATE_PATH)
    normalized_locale = normalize_locale(locale)
    total = snapshot.articles_total
    if total is None:
        total = len(snapshot.articles) + snapshot.articles_omitted
    included = len(snapshot.articles)

    def gettext(key: str, **values: Any) -> str:
        """Translate an administration template key for the current request locale."""
        return translate(normalized_locale, key, **values)

    return template.render(
        snapshot=snapshot,
        ticket=snapshot.ticket,
        articles=snapshot.articles,
        pdf_locale=normalized_locale,
        pdf_timezone=timezone,
        articles_total=total,
        articles_included=included,
        articles_omitted=snapshot.articles_omitted,
        _=gettext,
        plural_key=plural_key,
    )
