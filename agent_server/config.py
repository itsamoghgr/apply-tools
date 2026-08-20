"""Central configuration, read from environment (see agent_server/.env.example).

Kept deliberately flat and readable — one place to see every knob the service has.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

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


_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def parse_hhmm(value: str) -> tuple[int, int] | None:
    """"HH:MM" -> (hour, minute), or None when malformed.

    Returning None rather than raising is deliberate: a typo in a schedule knob
    must fall back to the default schedule, never stop the service from booting.
    """
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value or "")
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def parse_times(value: str) -> list[tuple[int, int]]:
    """"09:00,18:30" -> [(9,0),(18,30)], sorted, de-duplicated, bad entries dropped."""
    seen = {parse_hhmm(part) for part in (value or "").split(",") if part.strip()}
    return sorted(t for t in seen if t is not None)


def parse_days(value: str) -> str | None:
    """A day spec -> an APScheduler day_of_week string, or None for "every day".

    Accepts "mon-fri", "mon,wed,fri", "sat-sun" and "*". Anything unrecognised
    yields None, which APScheduler reads as every day — the safe direction, since
    a bad value should widen the schedule, never silently mute alerts.
    """
    raw = (value or "").strip().lower()
    if not raw or raw in ("*", "all", "daily", "everyday", "every day"):
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out: list[str] = []
    for part in parts:
        if "-" in part:
            start, _, end = part.partition("-")
            if start.strip() in _DAY_NAMES and end.strip() in _DAY_NAMES:
                out.append(f"{start.strip()}-{end.strip()}")
        elif part in _DAY_NAMES:
            out.append(part)
    return ",".join(out) or None


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

    # ── When to scrape, and when to alert ──────────────────────────────────
    # Every schedule knob below is interpreted in `jobboard_timezone`, NOT UTC
    # and not the machine's zone: "alert me at 09:00" has to mean 09:00 where
    # the user is, and must keep meaning that across a DST shift.
    jobboard_timezone: str = os.environ.get("JOBBOARD_TIMEZONE", "UTC")

    # SCRAPE: how often to re-check boards, and the window in which that is
    # allowed to happen. The active window exists because career pages publish
    # during business hours — scraping at 04:00 spends requests to learn nothing.
    # Leaving start == end means "no window", i.e. run around the clock.
    jobboard_monitor_interval_h: int = _int("JOBBOARD_MONITOR_INTERVAL_H", 3)
    jobboard_monitor_active_start: str = os.environ.get(
        "JOBBOARD_MONITOR_ACTIVE_START", "07:00"
    )
    jobboard_monitor_active_end: str = os.environ.get(
        "JOBBOARD_MONITOR_ACTIVE_END", "23:00"
    )
    jobboard_monitor_days: str = os.environ.get("JOBBOARD_MONITOR_DAYS", "*")

    # ALERT: explicit times-of-day beat an interval here. An interval drifts —
    # "every 6h" from a 02:14 boot mails at 02:14 forever — whereas a job hunter
    # wants the mail at a predictable hour. JOBBOARD_ALERT_AT, when set, wins;
    # the interval remains as the fallback for anyone who prefers it.
    jobboard_alert_interval_h: int = _int("JOBBOARD_ALERT_INTERVAL_H", 6)
    jobboard_alert_at: str = os.environ.get("JOBBOARD_ALERT_AT", "09:00,18:00")
    jobboard_alert_days: str = os.environ.get("JOBBOARD_ALERT_DAYS", "*")
    # Fallback pagination cap for custom (non-ATS) career pages. ATS boards
    # return everything in one call and never paginate.
    jobboard_max_pages: int = _int("JOBBOARD_MAX_PAGES", 10)
    # Prefer a same-day view when the source can provide one.
    jobboard_today_only: bool = (
        os.environ.get("JOBBOARD_TODAY_ONLY", "true").lower() == "true"
    )
    # Mail a "nothing new" heartbeat instead of skipping an empty alert.
    jobboard_alert_heartbeat: bool = (
        os.environ.get("JOBBOARD_ALERT_HEARTBEAT", "false").lower() == "true"
    )
    jobboard_company_sleep_min_s: float = float(
        os.environ.get("JOBBOARD_COMPANY_SLEEP_MIN_S", "1.0")
    )
    jobboard_company_sleep_max_s: float = float(
        os.environ.get("JOBBOARD_COMPANY_SLEEP_MAX_S", "3.0")
    )

    # Alert mail transport. A small self-contained SMTP sender lives in
    # jobboard/mailer.py — deliberately NOT a revival of backend/mail.py, whose
    # Gmail-inbox and click-tracking baggage was removed in 8cef324.
    jobboard_smtp_host: str = os.environ.get("JOBBOARD_SMTP_HOST", "smtp.gmail.com")
    jobboard_smtp_port: int = _int("JOBBOARD_SMTP_PORT", 465)
    jobboard_smtp_user: str | None = os.environ.get("JOBBOARD_SMTP_USER") or None
    jobboard_smtp_app_password: str | None = (
        os.environ.get("JOBBOARD_SMTP_APP_PASSWORD") or None
    )
    jobboard_alert_to: str | None = os.environ.get("JOBBOARD_ALERT_TO") or None

    @property
    def tzinfo(self) -> ZoneInfo:
        """`jobboard_timezone` as a real tzinfo, falling back to UTC.

        An unknown zone name must not stop the service booting, so a bad value
        degrades to UTC rather than raising out of Config construction.
        """
        try:
            return ZoneInfo(self.jobboard_timezone)
        except Exception:  # noqa: BLE001 — ZoneInfoNotFoundError + bad input
            return ZoneInfo("UTC")

    @property
    def monitor_hour_expr(self) -> str:
        """The active window as an APScheduler `hour` field.

        Built from the interval so both knobs still apply: an interval of 3 over
        a 07:00-23:00 window yields "7,10,13,16,19,22". A window whose start and
        end are equal (or unparseable) means no window, so every Nth hour of the
        day qualifies.
        """
        start = parse_hhmm(self.jobboard_monitor_active_start)
        end = parse_hhmm(self.jobboard_monitor_active_end)
        step = max(1, self.jobboard_monitor_interval_h)
        if start is None or end is None or start[0] == end[0]:
            return f"*/{step}"
        start_h, end_h = start[0], end[0]
        # An end before the start is an overnight window (e.g. 22:00-06:00).
        hours = (
            list(range(start_h, end_h + 1))
            if start_h <= end_h
            else list(range(start_h, 24)) + list(range(0, end_h + 1))
        )
        return ",".join(str(h) for h in hours[::step])

    @property
    def monitor_day_of_week(self) -> str | None:
        return parse_days(self.jobboard_monitor_days)

    @property
    def alert_times(self) -> list[tuple[int, int]]:
        """Explicit alert times-of-day; empty means "use the interval instead"."""
        return parse_times(self.jobboard_alert_at)

    @property
    def alert_day_of_week(self) -> str | None:
        return parse_days(self.jobboard_alert_days)

    @property
    def roster_role_keywords(self) -> frozenset[str]:
        """`roster_roles` as a lowercased keyword set for substring matching."""
        return frozenset(
            kw for kw in (p.strip().lower() for p in self.roster_roles.split(","))
            if kw
        )


CONFIG = Config()
