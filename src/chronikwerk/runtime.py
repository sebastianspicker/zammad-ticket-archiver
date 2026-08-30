"""Start the configured ASGI server for the command-line entry point."""

from __future__ import annotations

import uvicorn

from chronikwerk.composition import build_runtime_application


def main() -> int:
    """Start the configured service through the command-line entry point."""
    settings, app = build_runtime_application()
    uvicorn.run(
        app,
        host=settings.server.host,
        port=settings.server.port,
        log_config=None,
    )
    return 0
