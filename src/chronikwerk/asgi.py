"""Expose the ASGI application object used by production servers."""

from __future__ import annotations

from chronikwerk.composition import build_runtime_application

settings, app = build_runtime_application()
