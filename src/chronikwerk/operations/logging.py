"""Configure structured operational logging without exposing secrets."""

from __future__ import annotations

import io
import logging
import os
import sys
from typing import Any

import structlog
from structlog.stdlib import ProcessorFormatter

from chronikwerk.configuration.redaction import redact_settings_dict, scrub_secrets_in_text


def _scrub_event_dict(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return redact_settings_dict(event_dict)


def _resolve_log_format() -> str:
    return "human"


def _resolve_log_level(log_level_default: str) -> str:
    raw = (os.environ.get("LOG_LEVEL") or "").strip()
    if raw:
        return raw
    return log_level_default


def _coerce_log_format(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized if normalized in {"json", "human"} else None


def _redacted_exception_formatter(sio: Any, exc_info: Any) -> None:
    rendered = io.StringIO()
    structlog.dev.plain_traceback(rendered, exc_info)
    sio.write(scrub_secrets_in_text(rendered.getvalue()))


def _shared_processors(resolved_format: str) -> list[Any]:
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.StackInfoRenderer(),
        _scrub_event_dict,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if resolved_format == "json":
        processors.insert(4, structlog.processors.format_exc_info)
    return processors


def _renderer(resolved_format: str) -> Any:
    if resolved_format == "json":
        return structlog.processors.JSONRenderer()
    return structlog.dev.ConsoleRenderer(exception_formatter=_redacted_exception_formatter)


def _configure_root_logger(*, formatter: ProcessorFormatter, resolved_level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(resolved_level)


def _configure_noisy_loggers() -> None:
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(noisy)
        logger.handlers = []
        logger.propagate = True

    # WeasyPrint triggers verbose fontTools INFO logs during subsetting.
    # Keep app logs operationally useful by default.
    logging.getLogger("fontTools").setLevel(logging.WARNING)


def configure_logging(
    *,
    log_level: str = "INFO",
    log_format: str | None = None,
) -> None:
    """
    Minimal structlog + stdlib logging configuration.

    `log_format` may be "human" or "json".
    """
    resolved_level = _resolve_log_level(log_level).upper()
    configured_format = _coerce_log_format(log_format)
    resolved_format = configured_format or _resolve_log_format()

    shared_processors = _shared_processors(resolved_format)
    formatter = ProcessorFormatter(
        processor=_renderer(resolved_format),
        foreign_pre_chain=shared_processors,
    )

    _configure_root_logger(formatter=formatter, resolved_level=resolved_level)
    _configure_noisy_loggers()

    structlog.configure(
        processors=[
            *shared_processors,
            ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
