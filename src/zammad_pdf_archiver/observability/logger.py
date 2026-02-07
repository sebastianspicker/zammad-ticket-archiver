from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog
from structlog.stdlib import ProcessorFormatter

from zammad_pdf_archiver.config.redact import redact_settings_dict


def _scrub_event_dict(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return redact_settings_dict(event_dict)


def _resolve_log_format(json_logs_default: bool) -> str:
    raw = (os.environ.get("LOG_FORMAT") or "").strip().lower()
    if raw in {"json", "human"}:
        return raw
    return "json" if json_logs_default else "human"


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


def configure_logging(
    *,
    log_level: str = "INFO",
    json_logs: bool = False,
    log_format: str | None = None,
) -> None:
    """
    Minimal structlog + stdlib logging configuration.

    LOG_FORMAT=human|json can override `json_logs`.
    """
    resolved_level = _resolve_log_level(log_level).upper()
    configured_format = _coerce_log_format(log_format)
    resolved_format = configured_format or _resolve_log_format(json_logs)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _scrub_event_dict,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    renderer: Any
    if resolved_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    formatter = ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(resolved_level)

    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(noisy)
        logger.handlers = []
        logger.propagate = True

    # WeasyPrint triggers verbose fontTools INFO logs during subsetting.
    # Keep app logs operationally useful by default.
    logging.getLogger("fontTools").setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            *shared_processors,
            ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
