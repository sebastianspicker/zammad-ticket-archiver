"""Package-backed Jinja rendering for administration pages."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from chronikwerk.i18n import normalize_locale, translate


@lru_cache(maxsize=1)
def _environment() -> Environment:
    return Environment(
        loader=PackageLoader("chronikwerk.web", "templates/admin"),
        autoescape=select_autoescape(["html", "xml"]),
    )


def render_admin_template(template_name: str, *, locale: str, **context: Any) -> str:
    """Render an autoescaped admin template with request-local translations."""
    selected = normalize_locale(locale)

    def gettext(key: str, **values: Any) -> str:
        """Translate an administration template key for the current request locale."""
        return translate(selected, key, **values)

    return (
        _environment()
        .get_template(template_name)
        .render(
            locale=selected,
            _=gettext,
            **context,
        )
    )
