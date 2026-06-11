"""Tests for the deterministic verification gate (CONTRACTS §verification).

Providers are injected as fakes — no real Hunter/Abstract/SMTP calls.
"""

from __future__ import annotations

from agent_server.contracts.records import ResearchResult
from agent_server.stages.verify import (
    WaterfallVerifier,
    _linkedin_score,
    verify,
)
from agent_server.web.verifier import EmailVerdict


# --- LinkedIn plausibility -------------------------------------------------

def test_linkedin_score_valid_profile():
    assert _linkedin_score("https://www.linkedin.com/in/jane-doe") == 1.0
    assert _linkedin_score("https://linkedin.com/in/janedoe") == 1.0


def test_linkedin_score_rejects_non_profile():
    assert _linkedin_score(None) == 0.0
    assert _linkedin_score("") == 0.0
    assert _linkedin_score("https://linkedin.com/company/acme") == 0.0
    assert _linkedin_score("https://example.com/in/jane") == 0.0


# --- Waterfall ordering ----------------------------------------------------

class _FakeProvider:
    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = 0

    def find_and_verify(self, domain, founder_name):
        self.calls += 1
        return self.verdict


def test_waterfall_returns_first_provider_with_a_verdict():
    hit = EmailVerdict(email="a@acme.com", score=0.9, method="hunter", detail={})
    p1 = _FakeProvider(hit)
    p2 = _FakeProvider(EmailVerdict(email="b@acme.com", score=0.5, method="abstract", detail={}))
    v = WaterfallVerifier(provider_clients=[p1, p2], smtp_enabled=False)
    out = v.find_and_verify("acme.com", "Jane")
    assert out.method == "hunter"
    assert p1.calls == 1
    assert p2.calls == 0  # short-circuited


def test_waterfall_falls_through_none_to_next_provider():
    p1 = _FakeProvider(None)  # skipped (e.g. missing key)
    hit = EmailVerdict(email="b@acme.com", score=0.7, method="abstract", detail={})
    p2 = _FakeProvider(hit)
    v = WaterfallVerifier(provider_clients=[p1, p2], smtp_enabled=False)
    out = v.find_and_verify("acme.com", "Jane")
    assert out.method == "abstract"
    assert p1.calls == 1 and p2.calls == 1


def test_waterfall_no_providers_no_smtp_yields_none_verdict():
    v = WaterfallVerifier(provider_clients=[], smtp_enabled=False)
    out = v.find_and_verify("acme.com", "Jane")
    assert out.email is None
    assert out.score == 0.0
    assert out.method == "none"


def test_provider_exception_is_swallowed_and_falls_through():
    class _Boom:
        def find_and_verify(self, domain, founder_name):
            raise RuntimeError("api 500")

    hit = EmailVerdict(email="b@acme.com", score=0.6, method="abstract", detail={})
    v = WaterfallVerifier(provider_clients=[_Boom(), _FakeProvider(hit)], smtp_enabled=False)
    out = v.find_and_verify("acme.com", "Jane")
    assert out.method == "abstract"


# --- verify() end to end ---------------------------------------------------

def _rr(**kw):
    base = dict(domain="acme.com", name="Acme")
    base.update(kw)
    return ResearchResult(**base)


def test_verify_returns_confidence_in_unit_interval():
    verifier = WaterfallVerifier(provider_clients=[], smtp_enabled=False)
    rr = _rr(founder_name="Jane", founder_linkedin_url="https://linkedin.com/in/jane-doe",
             funding_stage="Seed")
    lead = verify("job1", rr, verifier=verifier)
    assert 0.0 <= lead.confidence <= 1.0
    assert lead.domain == "acme.com"
    assert lead.founder_email is None  # no provider found one
    # structure-only confidence: linkedin + founder + funding still > 0
    assert lead.confidence > 0.0


def test_verify_high_when_email_found():
    hit = EmailVerdict(email="jane@acme.com", score=0.95, method="hunter", detail={})
    verifier = WaterfallVerifier(provider_clients=[_FakeProvider(hit)], smtp_enabled=False)
    rr = _rr(founder_name="Jane", founder_linkedin_url="https://linkedin.com/in/jane-doe",
             funding_stage="Seed", funding_amount="$2M")
    lead = verify("job1", rr, verifier=verifier)
    assert lead.founder_email == "jane@acme.com"
    assert lead.confidence > 0.8
    assert lead.verification_detail["email_method"] == "hunter"


def test_verify_keyless_path_still_produces_a_lead():
    # No providers (missing keys) + no SMTP → still a VerifiedLead, low-ish score.
    verifier = WaterfallVerifier(provider_clients=[], smtp_enabled=False)
    rr = _rr()  # bare: no founder, no funding, no linkedin
    lead = verify("job1", rr, verifier=verifier)
    assert lead.confidence == 0.0  # nothing to score on
    assert lead.founder_email is None


def test_verify_never_raises_on_verifier_error():
    class _ExplodingVerifier(WaterfallVerifier):
        def find_and_verify(self, domain, founder_name):
            raise RuntimeError("kaboom")

    lead = verify("job1", _rr(founder_name="Jane"), verifier=_ExplodingVerifier())
    # error swallowed -> still a lead, email_method 'none'
    assert lead.verification_detail["email_method"] == "none"
    assert 0.0 <= lead.confidence <= 1.0
