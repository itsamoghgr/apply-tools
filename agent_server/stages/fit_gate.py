"""Cheap FIT GATE — runs BEFORE deep research on every discovered company.

Per discovered company the orchestrator calls `run_fit_gate` to decide whether
the company is worth the (expensive) deep-research pass. The judgment is against
a user-provided ICP (`fit_criteria`):

  - If `fit_criteria` is empty/whitespace → PASS-THROUGH (no LLM call, no skip).
    The pipeline then behaves exactly as it did before the gate existed.
  - Otherwise → ONE LLM call (no tools) scores the candidate against the ICP and
    returns {passed, score, reason}. `passed = score >= CONFIG.fit_threshold`.
    At most one cheap search is run, and ONLY when the candidate has no
    description to score from.

HARD rule (proved in tests): `run_fit_gate` NEVER raises. On ANY error it FAILS
OPEN (`passed=True`, reason="gate_error") so a gate bug can never silently drop
every discovered company. Matches the structlog + never-raise conventions of
contact.py / research.py.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent_server.agents.deps import AgentDeps
from agent_server.config import CONFIG
from agent_server.contracts.records import CandidateCompany, FitVerdict
from agent_server.log import get_logger

logger = get_logger(__name__)


_SYSTEM = (
    "You are a fast, decisive lead-qualification gate. Given a discovered company "
    "and a user's Ideal Customer Profile (ICP), score how well the company fits "
    "the ICP. This is a CHEAP first-pass filter: judge from the facts provided, "
    "do not overthink, and do not demand perfect information.\n\n"
    "Output ONLY a JSON object — no markdown fences, no prose:\n"
    '{"score": <float 0.0-1.0>, "reason": "<one short sentence>"}\n\n'
    "score = 1.0 means a strong, obvious fit; 0.0 means clearly off-target. Use "
    "the middle of the range when the company is plausible but the evidence is "
    "thin. Base the score ONLY on how well the company matches the ICP."
)


def _parse_llm_json(text: str) -> dict:
    """Tolerantly parse the gate LLM's JSON emission."""
    cleaned = re.sub(r"^```[a-z]*\n?", "", (text or "").strip(), flags=re.M)
    cleaned = re.sub(r"\n?```$", "", cleaned.strip(), flags=re.M).strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                pass
    return {}


def run_fit_gate(
    job_id: str,
    candidate: CandidateCompany,
    fit_criteria: str,
    *,
    deps: AgentDeps,
) -> FitVerdict:
    """Score `candidate` against `fit_criteria`; decide pass/skip.

    Never raises. Returns a FitVerdict; `passed=False` means the orchestrator
    should SKIP the company (record in seen-cache + audit, never save).
    """
    log = logger.bind(job_id=job_id, domain=candidate.domain)

    # ── PASS-THROUGH: no criteria → behave like today (no LLM, no skip). ──────
    if not fit_criteria or not fit_criteria.strip():
        log.debug("fit_gate_passthrough", reason="no_criteria")
        return FitVerdict(passed=True, score=1.0, reason="no_criteria")

    try:
        return _do_fit_gate(job_id, candidate, fit_criteria.strip(), deps, log)
    except Exception as exc:  # noqa: BLE001 — a gate bug must never drop everything
        log.warning("fit_gate_error", error=str(exc))
        try:
            deps.audit("fit", "gate_error", {"domain": candidate.domain, "error": str(exc)})
        except Exception:  # pragma: no cover - defensive
            pass
        # FAIL OPEN — pass the candidate through to research.
        return FitVerdict(passed=True, score=1.0, reason="gate_error")


def _do_fit_gate(
    job_id: str,
    candidate: CandidateCompany,
    fit_criteria: str,
    deps: AgentDeps,
    log: Any,
) -> FitVerdict:
    """Core gate logic — may raise; `run_fit_gate` catches and fails open."""

    description = (candidate.description or "").strip()

    # Optionally enrich with ONE cheap search, but ONLY when we have nothing to
    # score from (no description). Keeps the gate cheap (≤1 search, 1 LLM call).
    search_context = ""
    if not description:
        try:
            results = deps.search(
                f"{candidate.name} {candidate.domain} company what they do",
                max_results=3,
            )
            search_context = "\n".join(
                f"- {r.title}: {r.snippet}" for r in results[:3]
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("fit_gate_search_failed", error=str(exc))

    facts = [
        f"Company name: {candidate.name}",
        f"Domain: {candidate.domain}",
    ]
    if description:
        facts.append(f"Description: {description}")
    if candidate.funding_stage:
        facts.append(f"Funding stage: {candidate.funding_stage}")
    if candidate.funding_amount:
        facts.append(f"Funding amount: {candidate.funding_amount}")
    if search_context:
        facts.append(f"Web search snippets:\n{search_context}")

    user_msg = [
        {
            "role": "user",
            "content": (
                "ICP (Ideal Customer Profile) to score against:\n"
                f"{fit_criteria}\n\n"
                "Company facts:\n" + "\n".join(facts)
            ),
        }
    ]

    deps.audit("fit", "llm_call", {"domain": candidate.domain})
    response = deps.llm.complete(_SYSTEM, user_msg, tools=None)
    parsed = _parse_llm_json(response.get("text", ""))

    raw_score = parsed.get("score")
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        score = 0.0
    # Clamp to [0, 1].
    score = max(0.0, min(1.0, score))
    reason = str(parsed.get("reason") or "")

    passed = score >= CONFIG.fit_threshold
    log.info(
        "fit_gate_done",
        name=candidate.name,
        score=round(score, 3),
        passed=passed,
        threshold=CONFIG.fit_threshold,
    )
    return FitVerdict(passed=passed, score=score, reason=reason or f"fit:{score:.2f}")
