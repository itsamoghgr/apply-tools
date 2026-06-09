"""Reformat the Next.js dev server's stdout into our structlog format.

`next dev` prints human-formatted lines that don't match the backend's
structured log stream. start.sh pipes the frontend through this filter so the
whole `./start.sh` output renders consistently — console in dev, JSON when
ENV=production — using the *same* structlog pipeline as the Python backend.

We parse the one line shape that matters (per-request access logs) into real
fields; every other line (the Next banner, Ready, warnings, compile output) is
passed through as a structured event with its text preserved, so nothing is
lost. This is a thin presentation shim over Next's text output: if Next changes
its wording, request lines fall back to the pass-through path rather than break.

Usage:  next dev --turbopack 2>&1 | python -m frontend_log_filter
"""

from __future__ import annotations

import re
import sys

from log import configure_logging, get_logger

# e.g. " GET /applications 200 in 575ms (next.js: 112ms, application-code: 463ms)"
_REQUEST_RE = re.compile(
    r"^\s*(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+"
    r"(?P<path>\S+)\s+(?P<status>\d{3})\s+in\s+(?P<duration>\d+)ms"
    r"(?:\s+\((?P<timing>.*)\))?\s*$"
)

# ANSI color codes Next emits; strip so they don't leak into our fields.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_LEVEL = {
    "info": "info",
    "warn": "warning",
    "warning": "warning",
    "error": "error",
    "ready": "info",
    "event": "info",
    "wait": "info",
}


def _emit(logger, raw: str) -> None:
    line = _ANSI_RE.sub("", raw).rstrip("\n")
    if not line.strip():
        return

    m = _REQUEST_RE.match(line)
    if m:
        status = int(m["status"])
        fields = {
            "method": m["method"],
            "path": m["path"],
            "status": status,
            "duration_ms": int(m["duration"]),
            "source": "next",
        }
        if m["timing"]:
            fields["timing"] = m["timing"]
        # Match conventional access-log levels so you can filter on severity.
        level = "error" if status >= 500 else "warning" if status >= 400 else "info"
        getattr(logger, level)("http_request", **fields)
        return

    # Non-request line: keep the text, but honor a leading Next tag for level
    # (e.g. "warn  - ...", "error - ...") when present.
    level = "info"
    tag = line.split(None, 1)[0].lower().strip("-").strip() if line.split() else ""
    level = _LEVEL.get(tag, "info")
    getattr(logger, level)(line, source="next")


def main() -> None:
    configure_logging()
    logger = get_logger("frontend")
    for raw in sys.stdin:
        _emit(logger, raw)


if __name__ == "__main__":
    main()
