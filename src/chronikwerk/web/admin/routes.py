"""Aggregate multilingual HTML and JSON routes for the admin application."""

from __future__ import annotations

from fastapi import APIRouter

from chronikwerk.web.admin._config_routes import (
    register_config_api_routes,
    register_config_page_routes,
)
from chronikwerk.web.admin._page_routes import register_page_routes
from chronikwerk.web.admin._revision_routes import (
    register_revision_api_routes,
    register_revision_page_routes,
)
from chronikwerk.web.admin._status_routes import register_status_routes

__all__ = ["router"]

router = APIRouter(prefix="/admin", include_in_schema=False)


register_page_routes(router)
register_config_page_routes(router)
register_revision_page_routes(router)
register_status_routes(router)
register_config_api_routes(router)
register_revision_api_routes(router)
