"""Expose the ASGI application object used by production servers.

A refused configuration prints the problem list and exits with status 1 on import.
"""

from __future__ import annotations

from chronikwerk.composition import build_runtime_application_or_exit

settings, app = build_runtime_application_or_exit()
