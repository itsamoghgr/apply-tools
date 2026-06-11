"""structlog configuration for the agent server.

Mirrors the platform backend's approach (backend/log.py): one consistent stream,
stdlib + uvicorn loggers routed through structlog. Kept self-contained so the
agent server has no import dependency on the platform package.

Usage::

    from agent_server.logging import get_logger
    logger = get_logger(__name__)
    logger.info("hunt_started", job_id=job_id, target=50)

Env knobs: LOG_FORMAT ("json"|"console"), LOG_LEVEL, ENV ("production" defaults
LOG_FORMAT to json).
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog

_configured = False


def _log_level_name() -> str:
    return os.getenv("LOG_LEVEL", "INFO").upper()


def _log_level() -> int:
    return getattr(logging, _log_level_name(), logging.INFO)


def _use_json() -> bool:
    fmt = os.getenv("LOG_FORMAT", "").lower()
    if fmt in ("json", "console"):
        return fmt == "json"
    return os.getenv("ENV", "development").lower() == "production"


def _shared_processors() -> list:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]


def _render_processors() -> list:
    if _use_json():
        return [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    return [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
    ]


def _make_formatter() -> structlog.stdlib.ProcessorFormatter:
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors(),
        processors=_render_processors(),
    )


def configure_logging() -> None:
    """Configure structlog + the stdlib root logger. Idempotent."""
    global _configured
    if _configured:
        return

    structlog.configure(
        processors=[
            *_shared_processors(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(_log_level()),
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_make_formatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(_log_level())

    _configured = True


def build_uvicorn_log_config() -> dict[str, Any]:
    """dictConfig for uvicorn's --log-config, so uvicorn's own loggers render
    through the same structlog formatter."""
    level = _log_level_name()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"structlog": {"()": _make_formatter}},
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "structlog",
                "stream": "ext://sys.stderr",
            }
        },
        "root": {"handlers": ["default"], "level": level},
        "loggers": {
            "uvicorn": {"level": level, "handlers": [], "propagate": True},
            "uvicorn.error": {"level": level, "handlers": [], "propagate": True},
            "uvicorn.access": {"level": level, "handlers": [], "propagate": True},
        },
    }


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger. Safe to call at import time."""
    return structlog.get_logger(name)
