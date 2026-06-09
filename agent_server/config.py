"""Central configuration, read from environment (see agent_server/.env.example).

Kept deliberately flat and readable — one place to see every knob the service has.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load agent_server/.env if present (sits next to this package dir).
_ENV_PATH = Path(__file__).resolve().parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Config:
    # Server
    host: str = os.environ.get("AGENT_HOST", "0.0.0.0")
    port: int = _int("AGENT_PORT", 8001)

    # Agent DB (operational store) — SEPARATE Postgres DB from the platform.
    agent_database_url: str = os.environ.get(
        "AGENT_DATABASE_URL", "postgresql://apply:apply@localhost:5432/apply_agent"
    )

    # Platform API (client) — source of truth for verified leads.
    platform_api_base: str = os.environ.get(
        "PLATFORM_API_BASE", "http://localhost:8000"
    )
    platform_api_token: str | None = os.environ.get("PLATFORM_API_TOKEN") or None

    # Run shape
    target_count: int = _int("HUNT_TARGET_COUNT", 50)
    loop_sleep_min_s: float = float(os.environ.get("LOOP_SLEEP_MIN_S", "0.5"))
    loop_sleep_max_s: float = float(os.environ.get("LOOP_SLEEP_MAX_S", "2.0"))

    # LLM (runtime agents)
    anthropic_api_key: str | None = os.environ.get("ANTHROPIC_API_KEY") or None
    llm_model: str = os.environ.get("AGENT_LLM_MODEL", "claude-opus-4-8")

    # Structured-floor sources
    product_hunt_token: str | None = os.environ.get("PRODUCT_HUNT_TOKEN") or None
    yc_oss_url: str = os.environ.get(
        "YC_OSS_URL", "https://yc-oss.github.io/api/companies/all.json"
    )

    # Verification waterfall — comma-separated provider order, then SMTP fallback.
    verify_providers: str = os.environ.get("VERIFY_PROVIDERS", "hunter,abstract")
    hunter_api_key: str | None = os.environ.get("HUNTER_API_KEY") or None
    abstract_api_key: str | None = os.environ.get("ABSTRACT_API_KEY") or None
    smtp_fallback_enabled: bool = (
        os.environ.get("SMTP_FALLBACK_ENABLED", "true").lower() == "true"
    )


CONFIG = Config()
