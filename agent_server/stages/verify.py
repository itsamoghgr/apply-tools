"""Deterministic verification gate (§4 of the build spec).

verify(job_id, rr: ResearchResult) -> VerifiedLead

Pipeline:
  1. LinkedIn URL plausibility (structural check only — never fetches).
  2. Email discovery + verification via a provider WATERFALL:
       Providers tried in CONFIG.verify_providers order (default: hunter, abstract).
       Each provider: find_and_verify(domain, founder_name) -> EmailVerdict | None.
       None means "fall through to the next provider".
       After all providers exhausted → SMTP fallback (if enabled).
       If nothing yields a result → EmailVerdict(email=None, score=0, method="none").
  3. Combine sub-scores into a single `confidence` in [0, 1]:

       confidence = (
           0.55 * email_score          # primary signal
         + 0.20 * linkedin_score       # structural plausibility
         + 0.15 * has_founder_name     # 0 or 1
         + 0.10 * has_funding          # 0 or 1
       )

     Rationale: email deliverability is the strongest hiring/outreach signal.
     LinkedIn plausibility is a secondary structural indicator. Presence of
     founder name and funding data correlate with a well-researched target.
     Total weight = 1.0.

Confidence is always a float in [0, 1]. Delivery sends regardless of score.

SMTP notes (always weak):
  - Assumes residential/cloud port 25 is blocked → degrades gracefully.
  - Even if the MX handshake succeeds, most SMTP targets are accept-all
    (return 250 for any address), so a 250 only yields score 0.30.
  - Timeout, refused, or blocked → score 0, method 'none'.

Provider clients are injected (or instantiated with the global CONFIG) so
they are mockable in tests. The module-level `VERIFIER` uses CONFIG defaults.
"""

from __future__ import annotations

import re
import smtplib
import socket
from typing import Protocol, runtime_checkable

from agent_server.config import CONFIG
from agent_server.contracts.records import ResearchResult, VerifiedLead
from agent_server.log import get_logger
from agent_server.web.verifier import EmailVerdict, Verifier

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Confidence weighting (documented in module docstring)
# ---------------------------------------------------------------------------
_W_EMAIL = 0.55
_W_LINKEDIN = 0.20
_W_FOUNDER = 0.15
_W_FUNDING = 0.10


# ---------------------------------------------------------------------------
# LinkedIn plausibility scorer
# ---------------------------------------------------------------------------

# A valid public LinkedIn profile URL:
#   https://www.linkedin.com/in/<slug>
# slug: alphanumeric + hyphens, 3–100 chars.
_LINKEDIN_RE = re.compile(
    r"^https?://(?:www\.)?linkedin\.com/in/([A-Za-z0-9][A-Za-z0-9\-]{1,98}[A-Za-z0-9])/?$"
)


def _linkedin_score(url: str | None) -> float:
    """Return a score in [0, 1] for structural plausibility of the URL.

    0.0  → None / empty / wrong host / wrong path
    0.5  → correct host + /in/ path but slug looks short/suspicious
    1.0  → looks like a real public profile
    """
    if not url:
        return 0.0
    m = _LINKEDIN_RE.match(url.strip())
    if not m:
        return 0.0
    slug = m.group(1)
    # Slugs under 3 chars are unlikely real profiles
    if len(slug) < 3:
        return 0.5
    return 1.0


# ---------------------------------------------------------------------------
# Provider protocol (for test injection)
# ---------------------------------------------------------------------------


@runtime_checkable
class ProviderClient(Protocol):
    def find_and_verify(
        self, domain: str, founder_name: str | None
    ) -> EmailVerdict | None: ...


# ---------------------------------------------------------------------------
# Hunter.io provider
# ---------------------------------------------------------------------------


def _match_person(emails: list[dict], founder_name: str) -> dict | None:
    """Return the email entry that belongs to `founder_name`, or None.

    Matches on Hunter's first_name/last_name fields when present, else on the
    email local-part containing the person's first or last name. Avoids passing
    off a different employee as the requested person.
    """
    parts = [re.sub(r"[^a-z]", "", p) for p in founder_name.lower().split()]
    parts = [p for p in parts if len(p) >= 2]
    if not parts:
        return None
    first, last = parts[0], parts[-1]

    for e in emails:
        fn = re.sub(r"[^a-z]", "", str(e.get("first_name") or "").lower())
        ln = re.sub(r"[^a-z]", "", str(e.get("last_name") or "").lower())
        if fn and ln and fn == first and ln == last:
            return e  # strong: both names match

    # Weaker: the local part contains first or last name.
    for e in emails:
        local = str(e.get("value") or "").split("@")[0].lower()
        local = re.sub(r"[^a-z]", "", local)
        if last and last in local:
            return e
        if first and len(first) >= 3 and first in local:
            return e
    return None


class HunterProvider:
    """Calls Hunter.io domain-search + email-verifier endpoints.

    API shape (v2):
      GET https://api.hunter.io/v2/domain-search?domain=<>&full_name=<>&api_key=<>
      → { data: { emails: [ {value, confidence, ...} ] } }

      GET https://api.hunter.io/v2/email-verifier?email=<>&api_key=<>
      → { data: { result, score, ... } }

    Returns None (fall through) if:
      - API key is missing
      - Network error or non-2xx
      - No email found
    """

    _BASE = "https://api.hunter.io/v2"

    def __init__(self, api_key: str | None = None, http_client=None):
        self._key = api_key or CONFIG.hunter_api_key
        self._http = http_client  # injected in tests; None → use httpx directly

    def _get(self, url: str, params: dict) -> dict | None:
        """Perform a GET; return parsed JSON or None on error."""
        if self._http is not None:
            # Injected client (e.g. respx mock or simple wrapper)
            try:
                resp = self._http.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                logger.warning("hunter.request_error", url=url, error=str(exc))
                return None
        else:
            import httpx
            try:
                resp = httpx.get(url, params=params, timeout=10.0)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                logger.warning("hunter.request_error", url=url, error=str(exc))
                return None

    def find_and_verify(
        self, domain: str, founder_name: str | None
    ) -> EmailVerdict | None:
        if not self._key:
            return None  # no key → skip

        # Step 1: domain search for the founder's email
        params: dict = {"domain": domain, "api_key": self._key, "limit": "5"}
        if founder_name:
            params["full_name"] = founder_name

        data = self._get(f"{self._BASE}/domain-search", params)
        if data is None:
            return None

        emails = data.get("data", {}).get("emails", [])
        if not emails:
            logger.debug("hunter.no_emails", domain=domain)
            return None

        # Prefer an email that actually MATCHES the requested person. Hunter's
        # domain-search returns many people; picking max-confidence alone can
        # return a DIFFERENT employee (e.g. asking for "Bret Taylor" and getting
        # the top-confidence "jillian@"). Match on first/last name when given.
        name_match = None
        if founder_name:
            name_match = _match_person(emails, founder_name)

        if name_match is not None:
            best = name_match
            matched = True
        else:
            best = max(emails, key=lambda e: e.get("confidence", 0))
            matched = False

        best_email = best.get("value")
        hunter_confidence = best.get("confidence", 0)  # 0–100

        if not best_email:
            return None

        if founder_name and not matched:
            # We could not find this person's address — don't pass off a random
            # colleague as them. Skip so the agent/waterfall keeps looking.
            logger.info(
                "hunter.no_name_match",
                domain=domain,
                wanted=founder_name,
                top_email=best_email,
            )
            return None

        # Step 2: verify the address
        verify_data = self._get(
            f"{self._BASE}/email-verifier",
            {"email": best_email, "api_key": self._key},
        )
        if verify_data:
            v = verify_data.get("data", {})
            result = v.get("result", "unknown")  # "deliverable", "undeliverable", ...
            v_score = v.get("score", hunter_confidence)  # 0–100
        else:
            result = "unknown"
            v_score = hunter_confidence

        # Map to [0, 1]
        score = min(max(float(v_score) / 100.0, 0.0), 1.0)
        # Penalise explicitly undeliverable
        if result == "undeliverable":
            score = min(score, 0.1)

        logger.info(
            "hunter.verdict",
            domain=domain,
            email=best_email,
            result=result,
            score=round(score, 3),
        )
        return EmailVerdict(
            email=best_email,
            score=score,
            method="hunter",
            detail={"hunter_result": result, "hunter_score": v_score},
        )


# ---------------------------------------------------------------------------
# Abstract API provider
# ---------------------------------------------------------------------------


class AbstractProvider:
    """Calls AbstractAPI's email-validation endpoint.

    API shape:
      GET https://emailvalidation.abstractapi.com/v1/?api_key=<>&email=<>
      → { email, deliverability: "DELIVERABLE"|"UNDELIVERABLE"|"RISKY"|"UNKNOWN",
          quality_score: "0.00"–"1.00", ... }

    Strategy: we derive the email by guessing first@domain if a founder name
    is given (first-name pattern), otherwise info@domain as a weak probe.
    AbstractAPI does not search; it only validates a given address.

    Returns None if key missing or network error. Score comes from
    quality_score + deliverability mapping.
    """

    _BASE = "https://emailvalidation.abstractapi.com/v1/"

    def __init__(self, api_key: str | None = None, http_client=None):
        self._key = api_key or CONFIG.abstract_api_key
        self._http = http_client

    def _get(self, params: dict) -> dict | None:
        if self._http is not None:
            try:
                resp = self._http.get(self._BASE, params=params)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                logger.warning("abstract.request_error", error=str(exc))
                return None
        else:
            import httpx
            try:
                resp = httpx.get(self._BASE, params=params, timeout=10.0)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                logger.warning("abstract.request_error", error=str(exc))
                return None

    @staticmethod
    def _guess_email(domain: str, founder_name: str | None) -> str:
        """Best-guess email: first name if available, else info@."""
        if founder_name:
            parts = founder_name.strip().lower().split()
            if parts:
                first = re.sub(r"[^a-z0-9]", "", parts[0])
                if first:
                    return f"{first}@{domain}"
        return f"info@{domain}"

    def find_and_verify(
        self, domain: str, founder_name: str | None
    ) -> EmailVerdict | None:
        if not self._key:
            return None

        email = self._guess_email(domain, founder_name)
        data = self._get({"api_key": self._key, "email": email})
        if data is None:
            return None

        deliverability = data.get("deliverability", "UNKNOWN").upper()
        quality_raw = data.get("quality_score", "0")
        try:
            quality = float(quality_raw)
        except (TypeError, ValueError):
            quality = 0.0

        # Map deliverability to a score multiplier
        _DELIVER_MAP = {
            "DELIVERABLE": 1.0,
            "RISKY": 0.5,
            "UNKNOWN": 0.3,
            "UNDELIVERABLE": 0.05,
        }
        multiplier = _DELIVER_MAP.get(deliverability, 0.3)
        score = quality * multiplier
        score = min(max(score, 0.0), 1.0)

        logger.info(
            "abstract.verdict",
            domain=domain,
            email=email,
            deliverability=deliverability,
            quality=quality,
            score=round(score, 3),
        )
        return EmailVerdict(
            email=email,
            score=score,
            method="abstract",
            detail={
                "abstract_deliverability": deliverability,
                "abstract_quality": quality,
            },
        )


# ---------------------------------------------------------------------------
# Apollo.io People Match
# ---------------------------------------------------------------------------


class ApolloProvider:
    """Calls Apollo.io's People Match API to find a person's email.

    API shape:
      POST https://api.apollo.io/api/v1/people/match
        headers: { "X-Api-Key": <key> }
        json:    { "name": <full name>, "domain": <company domain>,
                   "reveal_personal_emails": false }
      → { person: { email, email_status, ... } }

    Unlike Abstract (validate-only), Apollo *finds* the address from its B2B
    database, so it is the strongest provider when a name + domain are known.
    Returns None if key missing, network error, or no person/email found.
    `email_status` ("verified"/"guessed"/...) maps to the score.
    """

    _URL = "https://api.apollo.io/api/v1/people/match"

    def __init__(self, api_key: str | None = None, http_client=None):
        self._key = api_key or CONFIG.apollo_api_key
        self._http = http_client

    def _post(self, payload: dict, headers: dict) -> dict | None:
        if self._http is not None:
            try:
                resp = self._http.post(self._URL, json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                logger.warning("apollo.request_error", error=str(exc))
                return None
        import httpx

        try:
            resp = httpx.post(self._URL, json=payload, headers=headers, timeout=12.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("apollo.request_error", error=str(exc))
            return None

    def find_and_verify(
        self, domain: str, founder_name: str | None
    ) -> EmailVerdict | None:
        if not self._key or not founder_name:
            return None  # Apollo match needs a name

        data = self._post(
            {"name": founder_name, "domain": domain, "reveal_personal_emails": False},
            {"X-Api-Key": self._key, "Content-Type": "application/json"},
        )
        if data is None:
            return None
        person = data.get("person") or {}
        email = person.get("email")
        if not email or "email_not_unlocked" in str(email):
            return None

        status = (person.get("email_status") or "").lower()
        # Map Apollo's email_status to a confidence score.
        _STATUS_MAP = {"verified": 0.95, "likely": 0.7, "guessed": 0.45, "": 0.5}
        score = _STATUS_MAP.get(status, 0.5)

        logger.info(
            "apollo.verdict", domain=domain, email=email, status=status, score=score
        )
        return EmailVerdict(
            email=email,
            score=score,
            method="apollo",
            detail={
                "apollo_email_status": status,
                "apollo_title": person.get("title"),
                "apollo_linkedin": person.get("linkedin_url"),
            },
        )


# ---------------------------------------------------------------------------
# SMTP fallback (always weak)
# ---------------------------------------------------------------------------

_SMTP_TIMEOUT = 5  # seconds


def _smtp_verify(domain: str, founder_name: str | None) -> EmailVerdict:
    """Weak MX + RCPT handshake.

    Assumptions (documented in module docstring):
      - Residential/cloud port 25 is typically blocked → most attempts fail.
      - Accept-all servers return 250 for any RCPT; score capped at 0.30.
      - Any timeout, refusal, or OS error → score 0, method 'none'.

    Never raises.
    """
    import dns.resolver  # type: ignore[import]  # dnspython; optional

    # Build probe address
    if founder_name:
        parts = founder_name.strip().lower().split()
        first = re.sub(r"[^a-z0-9]", "", parts[0]) if parts else ""
        probe_email = f"{first}@{domain}" if first else f"info@{domain}"
    else:
        probe_email = f"info@{domain}"

    # MX lookup
    try:
        mx_records = dns.resolver.resolve(domain, "MX")
        mx_host = str(sorted(mx_records, key=lambda r: r.preference)[0].exchange).rstrip(".")
    except Exception as exc:
        logger.debug("smtp.mx_lookup_failed", domain=domain, error=str(exc))
        return EmailVerdict(email=None, score=0.0, method="none", detail={"smtp_error": str(exc)})

    # SMTP handshake
    try:
        with smtplib.SMTP(timeout=_SMTP_TIMEOUT) as smtp:
            smtp.connect(mx_host, 25)
            smtp.ehlo_or_helo_if_needed()
            smtp.mail("probe@example.com")
            code, _ = smtp.rcpt(probe_email)
            if code == 250:
                # Accept-all is common; only a low score
                logger.debug("smtp.rcpt_250", domain=domain, email=probe_email)
                return EmailVerdict(
                    email=probe_email,
                    score=0.30,
                    method="smtp",
                    detail={"smtp_rcpt_code": 250, "smtp_accept_all_assumed": True},
                )
            else:
                return EmailVerdict(
                    email=None,
                    score=0.0,
                    method="smtp",
                    detail={"smtp_rcpt_code": code},
                )
    except (smtplib.SMTPException, socket.error, OSError, TimeoutError) as exc:
        logger.debug("smtp.handshake_failed", domain=domain, error=str(exc))
        return EmailVerdict(
            email=None,
            score=0.0,
            method="none",
            detail={"smtp_error": str(exc)},
        )


# ---------------------------------------------------------------------------
# WaterfallVerifier — the main Verifier implementation
# ---------------------------------------------------------------------------


class WaterfallVerifier(Verifier):
    """Tries providers in CONFIG.verify_providers order, then SMTP fallback.

    Providers are instantiated lazily so missing API keys silently skip them.
    Pass `provider_clients` to inject mocks in tests.
    """

    def __init__(
        self,
        provider_clients: list[ProviderClient] | None = None,
        *,
        smtp_enabled: bool | None = None,
    ) -> None:
        self._providers = provider_clients  # None → build from CONFIG
        self._smtp_enabled = (
            smtp_enabled if smtp_enabled is not None else CONFIG.smtp_fallback_enabled
        )

    def _build_providers(self) -> list[ProviderClient]:
        """Build provider list from CONFIG.verify_providers."""
        order = [p.strip().lower() for p in CONFIG.verify_providers.split(",") if p.strip()]
        clients: list[ProviderClient] = []
        for name in order:
            if name == "hunter":
                clients.append(HunterProvider())
            elif name == "abstract":
                clients.append(AbstractProvider())
            elif name == "apollo":
                clients.append(ApolloProvider())
            else:
                logger.warning("verify.unknown_provider", name=name)
        return clients

    def find_and_verify(self, domain: str, founder_name: str | None) -> EmailVerdict:
        providers = self._providers if self._providers is not None else self._build_providers()

        for provider in providers:
            try:
                verdict = provider.find_and_verify(domain, founder_name)
            except Exception as exc:
                logger.warning(
                    "verify.provider_error",
                    provider=type(provider).__name__,
                    domain=domain,
                    error=str(exc),
                )
                verdict = None

            if verdict is not None:
                return verdict

        # SMTP fallback
        if self._smtp_enabled:
            try:
                import dns.resolver  # noqa: F401 — check availability
                return _smtp_verify(domain, founder_name)
            except ImportError:
                logger.debug("verify.smtp_skipped_no_dnspython", domain=domain)
            except Exception as exc:
                logger.warning("verify.smtp_error", domain=domain, error=str(exc))

        return EmailVerdict(email=None, score=0.0, method="none", detail={})


# ---------------------------------------------------------------------------
# Module-level singleton (uses CONFIG defaults)
# ---------------------------------------------------------------------------

_DEFAULT_VERIFIER = WaterfallVerifier()


# ---------------------------------------------------------------------------
# Public gate function
# ---------------------------------------------------------------------------


def verify(
    job_id: str,
    rr: ResearchResult,
    *,
    verifier: WaterfallVerifier | None = None,
) -> VerifiedLead:
    """Run verification pipeline and return a VerifiedLead.

    Never raises — any internal error yields a low-confidence lead so the
    pipeline continues.

    Parameters
    ----------
    job_id:   for logging / audit context
    rr:       ResearchResult from the research agent
    verifier: injectable WaterfallVerifier (for tests); defaults to the
              module-level singleton using CONFIG
    """
    _v = verifier or _DEFAULT_VERIFIER

    # Sub-score 1: LinkedIn plausibility
    linkedin_score = _linkedin_score(rr.founder_linkedin_url)

    # Sub-score 2: email via waterfall
    try:
        verdict: EmailVerdict = _v.find_and_verify(rr.domain, rr.founder_name)
    except Exception as exc:
        logger.error(
            "verify.waterfall_unexpected_error",
            job_id=job_id,
            domain=rr.domain,
            error=str(exc),
        )
        verdict = EmailVerdict(email=None, score=0.0, method="none", detail={"error": str(exc)})

    # Sub-scores 3+4: presence booleans
    has_founder = 1.0 if rr.founder_name else 0.0
    has_funding = 1.0 if (rr.funding_stage or rr.funding_amount) else 0.0

    # Combined confidence
    confidence = (
        _W_EMAIL * verdict.score
        + _W_LINKEDIN * linkedin_score
        + _W_FOUNDER * has_founder
        + _W_FUNDING * has_funding
    )
    confidence = round(min(max(confidence, 0.0), 1.0), 4)

    verification_detail: dict = {
        "email_score": round(verdict.score, 4),
        "email_method": verdict.method,
        "email_detail": verdict.detail,
        "linkedin_score": round(linkedin_score, 4),
        "has_founder_name": bool(has_founder),
        "has_funding": bool(has_funding),
        "weights": {
            "email": _W_EMAIL,
            "linkedin": _W_LINKEDIN,
            "founder": _W_FOUNDER,
            "funding": _W_FUNDING,
        },
    }

    logger.info(
        "verify.done",
        job_id=job_id,
        domain=rr.domain,
        email=verdict.email,
        email_method=verdict.method,
        confidence=confidence,
    )

    return VerifiedLead(
        domain=rr.domain,
        name=rr.name,
        funding_stage=rr.funding_stage,
        funding_amount=rr.funding_amount,
        founder_name=rr.founder_name,
        founder_linkedin_url=rr.founder_linkedin_url,
        founder_email=verdict.email,
        employee_count=rr.employee_count,
        revenue=rr.revenue,
        location=rr.location,
        industry=rr.industry,
        last_round_date=rr.last_round_date,
        confidence=confidence,
        verification_detail=verification_detail,
        sources=rr.sources,
    )
