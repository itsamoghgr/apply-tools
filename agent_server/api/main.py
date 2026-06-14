"""Uvicorn entrypoint for the agent server.

Run with:
    python -m agent_server.api.main

Or via uvicorn directly:
    uvicorn agent_server.api.app:app --host 0.0.0.0 --port 8002
"""

from __future__ import annotations

import uvicorn

from agent_server.config import CONFIG
from agent_server.log import build_uvicorn_log_config


def main() -> None:
    uvicorn.run(
        "agent_server.api.app:app",
        host=CONFIG.host,
        port=CONFIG.port,
        log_config=build_uvicorn_log_config(),
    )


if __name__ == "__main__":
    main()
