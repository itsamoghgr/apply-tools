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
    port: int = _int("AGENT_PORT", 8002)

    # Agent DB (operational store) — SEPARATE Postgres DB from the platform.
    agent_database_url: str = os.environ.get(
        "AGENT_DATABASE_URL", "postgresql://apply:apply@localhost:5432/apply_agent"
    )

    # Platform API (client) — source of truth for verified leads.
    platform_api_base: str = os.environ.get(
        "PLATFORM_API_BASE", "http://localhost:8001"
    )
    platform_api_token: str | None = os.environ.get("PLATFORM_API_TOKEN") or None

    # Run shape
    target_count: int = _int("HUNT_TARGET_COUNT", 50)
    loop_sleep_min_s: float = float(os.environ.get("LOOP_SLEEP_MIN_S", "0.5"))
    loop_sleep_max_s: float = float(os.environ.get("LOOP_SLEEP_MAX_S", "2.0"))

    # Fit gate + deep research. The cheap fit gate scores a discovered company
    # against the user's ICP (fit_criteria); a candidate PASSES when its score is
    # >= fit_threshold, otherwise it is SKIPPED before any deep research runs.
    # deep_research_tool_budget caps the research agent's LLM-driven tool calls.
    fit_threshold: float = float(os.environ.get("FIT_THRESHOLD", "0.4"))
    deep_research_tool_budget: int = _int("DEEP_RESEARCH_TOOL_BUDGET", 20)

    # LLM (runtime agents). Provider is "bedrock" or "anthropic".
    #   - bedrock  → AWS Bedrock Claude via the standard AWS credential chain
    #     (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / ~/.aws / IAM role). Uses a
    #     region-prefixed inference profile id (us.anthropic.claude-*).
    #   - anthropic → direct Anthropic API with ANTHROPIC_API_KEY.
    # Defaults to bedrock when AWS creds are present and no direct Anthropic key
    # is set, mirroring the platform backend's default.
    llm_provider: str = os.environ.get("AGENT_LLM_PROVIDER", "").lower() or (
        "bedrock"
        if (os.environ.get("AWS_ACCESS_KEY_ID") and not os.environ.get("ANTHROPIC_API_KEY"))
        else "anthropic"
    )
    anthropic_api_key: str | None = os.environ.get("ANTHROPIC_API_KEY") or None
    bedrock_region: str = (
        os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION") or "us-east-1"
    )
    bedrock_model: str = os.environ.get(
        "BEDROCK_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    )
    # Model id used when llm_provider == "anthropic" (direct API).
    llm_model: str = os.environ.get("AGENT_LLM_MODEL", "claude-opus-4-8")

    # Google Gemini (AGENT_LLM_PROVIDER=gemini). Uses an AI Studio API key via
    # Gemini's OpenAI-compatible endpoint — NOT Vertex AI, which authenticates
    # with ADC/service accounts instead. Key: https://aistudio.google.com/apikey
    gemini_api_key: str | None = os.environ.get("GEMINI_API_KEY") or None
    gemini_model: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    # Vertex AI (AGENT_LLM_PROVIDER=vertex) — the same Gemini models billed to a
    # GCP project, authenticated with Application Default Credentials instead of
    # an API key. Required when an org policy disallows API keys.
    #   gcloud auth application-default login
    #   gcloud config set project <PROJECT_ID>
    vertex_project: str | None = os.environ.get("VERTEX_PROJECT") or None
    vertex_location: str = os.environ.get("VERTEX_LOCATION", "us-central1")
    vertex_model: str = os.environ.get("VERTEX_MODEL", "google/gemini-2.5-flash")

    # Structured-floor sources
    product_hunt_token: str | None = os.environ.get("PRODUCT_HUNT_TOKEN") or None
    yc_oss_url: str = os.environ.get(
        "YC_OSS_URL", "https://yc-oss.github.io/api/companies/all.json"
    )

    # Verification waterfall — comma-separated provider order, then SMTP fallback.
    # Apollo finds the address (people match), Hunter finds + verifies, Abstract
    # validates a guessed address. Order them strongest-first.
    verify_providers: str = os.environ.get(
        "VERIFY_PROVIDERS", "apollo,hunter,abstract"
    )
    hunter_api_key: str | None = os.environ.get("HUNTER_API_KEY") or None
    abstract_api_key: str | None = os.environ.get("ABSTRACT_API_KEY") or None
    apollo_api_key: str | None = os.environ.get("APOLLO_API_KEY") or None
    smtp_fallback_enabled: bool = (
        os.environ.get("SMTP_FALLBACK_ENABLED", "true").lower() == "true"
    )

    # Roster ("find people at a company") role filter. High-signal hiring/leader
    # titles only — comma-separated, ROSTER_ROLES-overridable. Matched as
    # case-insensitive substrings against a person's title/position (see
    # `roster_role_keywords`).
    roster_roles: str = os.environ.get(
        "ROSTER_ROLES",
        "recruiter,talent,recruiting,sourcer,people,hr,hiring manager,"
        "head of engineering,vp engineering,engineering manager,cto,"
        "director of engineering,founder",
    )

    # ── Job Board (career-page monitor) ────────────────────────────────────
    # A watchlist-driven pipeline, separate from the lead-gen hunt above: it
    # only visits career pages the user explicitly added. See
    # docs/job_board_design.md.
    jobboard_enabled: bool = (
        os.environ.get("JOBBOARD_ENABLED", "true").lower() == "true"
    )
    jobboard_monitor_interval_h: int = _int("JOBBOARD_MONITOR_INTERVAL_H", 3)
    jobboard_digest_interval_h: int = _int("JOBBOARD_DIGEST_INTERVAL_H", 6)
    # Fallback pagination cap for custom (non-ATS) career pages. ATS boards
    # return everything in one call and never paginate.
    jobboard_max_pages: int = _int("JOBBOARD_MAX_PAGES", 10)
    # Prefer a same-day view when the source can provide one.
    jobboard_today_only: bool = (
        os.environ.get("JOBBOARD_TODAY_ONLY", "true").lower() == "true"
    )
    # Mail a "nothing new" heartbeat instead of skipping an empty digest.
    jobboard_digest_heartbeat: bool = (
        os.environ.get("JOBBOARD_DIGEST_HEARTBEAT", "false").lower() == "true"
    )
    jobboard_company_sleep_min_s: float = float(
        os.environ.get("JOBBOARD_COMPANY_SLEEP_MIN_S", "1.0")
    )
    jobboard_company_sleep_max_s: float = float(
        os.environ.get("JOBBOARD_COMPANY_SLEEP_MAX_S", "3.0")
    )

    # Digest mail transport. A small self-contained SMTP sender lives in
    # jobboard/mailer.py — deliberately NOT a revival of backend/mail.py, whose
    # Gmail-inbox and click-tracking baggage was removed in 8cef324.
    jobboard_smtp_host: str = os.environ.get("JOBBOARD_SMTP_HOST", "smtp.gmail.com")
    jobboard_smtp_port: int = _int("JOBBOARD_SMTP_PORT", 465)
    jobboard_smtp_user: str | None = os.environ.get("JOBBOARD_SMTP_USER") or None
    jobboard_smtp_app_password: str | None = (
        os.environ.get("JOBBOARD_SMTP_APP_PASSWORD") or None
    )
    jobboard_digest_to: str | None = os.environ.get("JOBBOARD_DIGEST_TO") or None

    @property
    def roster_role_keywords(self) -> frozenset[str]:
        """`roster_roles` as a lowercased keyword set for substring matching."""
        return frozenset(
            kw for kw in (p.strip().lower() for p in self.roster_roles.split(","))
            if kw
        )


CONFIG = Config()
