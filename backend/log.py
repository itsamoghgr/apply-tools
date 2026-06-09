"""Centralized structlog configuration for the backend.

Two entry points, both producing one consistent log stream:

* :func:`configure_logging` — called once on import of ``server`` (and by any
  script that imports our modules). Configures structlog and routes the stdlib
  root logger through structlog's renderer, so library logs (httpx, sqlalchemy,
  anthropic, ...) match our own events.

* :func:`build_uvicorn_log_config` — returns a ``logging.config.dictConfig``
  dict that uvicorn loads via ``--log-config``. This is what makes uvicorn's
  *own* loggers (the ``--reload`` supervisor banner, ``uvicorn.error``, and the
  ``uvicorn.access`` request log) render through structlog too. Import-time
  setup alone can't cover these because the reload supervisor starts before
  ``server.py`` is ever imported.

Everywhere else, get a logger with::

    from log import get_logger
    logger = get_logger(__name__)

and emit structured events::

    logger.warning("provider_fallback", provider=prov, next_provider=...)

Output format is controlled by env vars so the same code runs in dev and prod:

  LOG_FORMAT  "json" or "console". If unset, defaults to "json" when
              ENV=production, else "console".
  LOG_LEVEL   "DEBUG" | "INFO" (default) | "WARNING" | "ERROR".
  ENV         "production" | "development" (default). Drives the LOG_FORMAT
              default when LOG_FORMAT is unset.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import Any

import structlog

_configured = False

# Column (in visible chars) where the trailing `source=...` field is pinned in
# console output. Sized to clear the timestamp+level prefix (~40 chars) plus a
# typical message; longer lines push it right rather than truncating. Override
# with LOG_SOURCE_COLUMN.
_SOURCE_COLUMN = int(os.getenv("LOG_SOURCE_COLUMN", "92"))

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Dim color for the appended source tag, matching ConsoleRenderer's key style.
_DIM = "\x1b[2m"
_RESET = "\x1b[0m"


class _ConsoleRendererPinnedSource:
    """ConsoleRenderer wrapper that right-pins ``source`` to a fixed column.

    structlog's ConsoleRenderer renders key-values alphabetically right after
    the (padded) event message, so ``source`` neither stays last nor lands at a
    stable column. This wrapper pulls ``source`` out of the event before the
    inner render, then appends it as the final field padded to ``_SOURCE_COLUMN``
    (overflowing right on long lines). Padding is computed on the visible
    (ANSI-stripped) width so colors don't skew alignment.
    """

    def __init__(self, colors: bool) -> None:
        self._colors = colors
        self._inner = structlog.dev.ConsoleRenderer(colors=colors)

    def __call__(self, logger, method_name, event_dict):
        source = event_dict.pop("source", None)
        rendered = self._inner(logger, method_name, event_dict)
        if source is None:
            return rendered
        visible = len(_ANSI_RE.sub("", rendered))
        pad = max(1, _SOURCE_COLUMN - visible)
        if self._colors:
            tag = f"{_DIM}source={_RESET}{source}"
        else:
            tag = f"source={source}"
        return rendered + " " * pad + tag


def _log_level_name() -> str:
    return os.getenv("LOG_LEVEL", "INFO").upper()


def _log_level() -> int:
    return getattr(logging, _log_level_name(), logging.INFO)


def use_json() -> bool:
    fmt = os.getenv("LOG_FORMAT", "").lower()
    if fmt in ("json", "console"):
        return fmt == "json"
    # Default by environment: structured JSON in prod, human console elsewhere.
    return os.getenv("ENV", "development").lower() == "production"


def _shared_processors() -> list:
    """Processors applied to every record before the final renderer.

    Shared between structlog-native events and stdlib-routed (foreign) records
    so both carry the same timestamp/level/context fields.
    """
    return [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]


def _render_processors() -> list:
    """Final processors for the ProcessorFormatter, selected by output format."""
    if use_json():
        # dict_tracebacks -> structured "exception" field; JSON one line/event.
        return [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    # ConsoleRenderer formats key-values for humans and prints tracebacks;
    # the wrapper pins a trailing `source=...` to a fixed column.
    return [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        _ConsoleRendererPinnedSource(colors=sys.stderr.isatty()),
    ]


def _make_formatter() -> structlog.stdlib.ProcessorFormatter:
    """The single stdlib formatter every handler uses."""
    return structlog.stdlib.ProcessorFormatter(
        # foreign_pre_chain runs on records that did NOT originate from structlog
        # (i.e. plain stdlib logging from uvicorn / third-party libs).
        foreign_pre_chain=_shared_processors(),
        processors=_render_processors(),
    )


def _configure_structlog() -> None:
    structlog.configure(
        processors=[
            *_shared_processors(),
            # Hand off to the ProcessorFormatter so stdlib records and structlog
            # records share one rendering pipeline.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(_log_level()),
        cache_logger_on_first_use=True,
    )


def configure_logging() -> None:
    """Configure structlog + the stdlib root logger. Idempotent."""
    global _configured
    if _configured:
        return

    _configure_structlog()

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_make_formatter())

    root = logging.getLogger()
    # Replace any pre-existing handlers (e.g. a stray basicConfig) so we don't
    # double-log. Uvicorn's loggers are handled via build_uvicorn_log_config().
    root.handlers = [handler]
    root.setLevel(_log_level())

    _configured = True


def build_uvicorn_log_config() -> dict[str, Any]:
    """Return a dictConfig for uvicorn's ``--log-config``.

    Wires uvicorn's own loggers to our structlog ProcessorFormatter so the
    reload supervisor banner, error log, and access log all render in the same
    format as application events — no un-structured leakage. The ``()`` key is
    dictConfig's factory syntax: it calls ``_make_formatter`` to build the
    formatter, which is how we inject a structlog formatter object.
    """
    level = _log_level_name()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structlog": {"()": _make_formatter},
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "structlog",
                "stream": "ext://sys.stderr",
            },
        },
        "root": {"handlers": ["default"], "level": level},
        "loggers": {
            "uvicorn": {"level": level, "handlers": [], "propagate": True},
            "uvicorn.error": {"level": level, "handlers": [], "propagate": True},
            # access logs are noisy at INFO but standard; keep them, structured.
            "uvicorn.access": {"level": level, "handlers": [], "propagate": True},
        },
    }


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger. Safe to call at import time."""
    return structlog.get_logger(name)
