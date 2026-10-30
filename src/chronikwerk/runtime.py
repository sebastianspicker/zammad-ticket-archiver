"""Start the configured ASGI server for the command-line entry point."""

from __future__ import annotations

import uvicorn

from chronikwerk.composition import build_runtime_application_or_exit


def main() -> int:
    """Start the configured service through the command-line entry point.

    A refused configuration prints the problem list and exits with status 1.
    """
    settings, app = build_runtime_application_or_exit()
    uvicorn.run(
        app,
        host=settings.server.host,
        port=settings.server.port,
        log_config=None,
    )
    return 0
