from __future__ import annotations

import logging

from zammad_pdf_archiver.observability.logger import configure_logging


def test_configure_logging_reduces_fonttools_noise() -> None:
    configure_logging(log_level="INFO", json_logs=False)
    logger = logging.getLogger("fontTools")
    assert logger.getEffectiveLevel() >= logging.WARNING
