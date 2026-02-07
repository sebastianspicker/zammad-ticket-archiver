from __future__ import annotations

import uvicorn

from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.load import load_settings
from zammad_pdf_archiver.observability.logger import configure_logging


def main() -> int:
    settings = load_settings()
    configure_logging(
        log_level=settings.observability.log_level,
        log_format=settings.observability.log_format,
        json_logs=settings.observability.json_logs,
    )

    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.server.host,
        port=settings.server.port,
        log_config=None,
    )
    return 0

