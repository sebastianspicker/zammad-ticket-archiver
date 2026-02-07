from __future__ import annotations

import io
import json
import logging
import warnings

import structlog

from zammad_pdf_archiver.observability.logger import configure_logging


def test_configure_logging_reduces_fonttools_noise() -> None:
    configure_logging(log_level="INFO", json_logs=False)
    logger = logging.getLogger("fontTools")
    assert logger.getEffectiveLevel() >= logging.WARNING


def test_human_logging_does_not_emit_format_exc_info_warning() -> None:
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        configure_logging(log_level="INFO", json_logs=False, log_format="human")
        logger = structlog.get_logger("test.logger")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            logger.exception("expected_exception")

    assert not any(
        "Remove `format_exc_info` from your processor chain" in str(warning.message)
        for warning in captured
    )


def test_human_logging_redacts_secrets_in_exception_traceback() -> None:
    configure_logging(log_level="INFO", json_logs=False, log_format="human")

    stream = io.StringIO()
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.stream = stream

    logger = structlog.get_logger("test.logger")
    try:
        raise RuntimeError("Authorization: Bearer topsecret token=abc123")
    except RuntimeError:
        logger.exception("expected_exception")

    output = stream.getvalue()
    assert "topsecret" not in output
    assert "abc123" not in output


def test_json_logging_redacts_secrets_in_exception_traceback() -> None:
    configure_logging(log_level="INFO", json_logs=True, log_format="json")

    stream = io.StringIO()
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.stream = stream

    logger = structlog.get_logger("test.logger")
    try:
        raise RuntimeError("Authorization: Bearer topsecret token=abc123")
    except RuntimeError:
        logger.exception("expected_exception")

    payload = json.loads(stream.getvalue())
    rendered = json.dumps(payload)
    assert "topsecret" not in rendered
    assert "abc123" not in rendered
