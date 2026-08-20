"""Role-matching and dedup-key tests.

Role matching is the difference between "I saw the job" and "I missed it", so
these lean on real title shapes rather than synthetic ones.
"""

from __future__ import annotations

import pytest

from agent_server.jobboard.adapters.base import RawPosting
from agent_server.jobboard.matcher import (
    role_head,
    DEFAULT_ROLE_MATCHERS,
    dedup_key,
    normalize_title,
    role_match,
)


def posting(**kw) -> RawPosting:
    base = dict(external_id=None, title="AI Engineer", location="NYC",
                url="https://x/jobs/1", posted_at=None, source="generic")
    base.update(kw)
    return RawPosting(**base)


# ---------------------------------------------------------------------------
# normalize_title
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Senior AI Engineer II (REQ-4821)", "senior ai engineer"),
    ("AI Engineer, Remote [Contract]", "ai engineer remote"),
    ("  Data   Scientist  ", "data scientist"),
    ("Forward-Deployed Engineer", "forward deployed engineer"),
    ("Software Engineer III", "software engineer"),
    ("", ""),
])
def test_normalize_title(raw, expected):
    assert normalize_title(raw) == expected


def test_normalization_collapses_listing_variants():
    """The same role written three ways must normalise to one string."""
    variants = [
        "Senior AI Engineer II (REQ-4821)",
        "Senior AI Engineer  II",
        "Senior AI Engineer (Req #4821)",
    ]
    assert len({normalize_title(v) for v in variants}) == 1


# ---------------------------------------------------------------------------
# role_match — the four target roles
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    # Data Scientist
    ("Data Scientist", "Data Scientist"),
    ("Senior Data Scientist, Ads", "Data Scientist"),
    ("Applied Scientist II", "Data Scientist"),
    ("Research Scientist, ML", "Data Scientist"),
    # Data Analyst
    ("Data Analyst", "Data Analyst"),
    ("Senior Data Analyst", "Data Analyst"),
    ("Analytics Engineer", "Data Analyst"),
    ("Data Analytics Manager", "Data Analyst"),
    # AI Engineer
    ("AI Engineer", "AI Engineer"),
    ("Senior AI Engineer II (REQ-4821)", "AI Engineer"),
    ("Machine Learning Engineer", "AI Engineer"),
    ("ML Engineer - Remote", "AI Engineer"),
    ("Applied AI Engineer", "AI Engineer"),
    ("LLM Engineer", "AI Engineer"),
    ("GenAI Engineer", "AI Engineer"),
    # Founding Engineer
    ("Founding Engineer", "Founding Engineer"),
    ("Founding Software Engineer", "Founding Engineer"),
    ("Member of Technical Staff", "Founding Engineer"),
    # Forward Deployed Engineer
    ("Forward Deployed Engineer", "Forward Deployed Engineer"),
    ("Forward-Deployed Engineer", "Forward Deployed Engineer"),
    ("Forward Deployed AI Engineer", "Forward Deployed Engineer"),
    ("FDE", "Forward Deployed Engineer"),
    ("Deployment Strategist", "Forward Deployed Engineer"),
    ("Solutions Engineer", "Forward Deployed Engineer"),
])
def test_role_match_hits(title, expected):
    assert role_match(title) == expected


@pytest.mark.parametrize("title", [
    "Software Engineer",
    "Product Manager",
    "Account Executive, Commercial",
    "Frontend Engineer",
    "Recruiter",
    "Database Administrator",     # 'data' must not fire inside 'database'
    # Analyst roles that are not data-analyst work. The Data Analyst aliases are
    # scoped to data-specific phrases so a bare "analyst" can't drag these in.
    "Marketing Analyst",
    "Financial Analyst",
    "Business Analyst",
    "Product Analyst",
    # BI / insights / reporting analyst are deliberately NOT Data Analyst.
    "BI Analyst",
    "Business Intelligence Analyst",
    "Insights Analyst",
    "Reporting Analyst",
    "",
])
def test_role_match_misses(title):
    assert role_match(title) is None


@pytest.mark.parametrize("title", [
    "AI Engineering Intern",
    "Data Science Internship",
    "Founding Engineer - Co-op",
    "Machine Learning Engineer, New Grad",
    "Working Student, Data Science",
    "Data Analyst Intern",
])
def test_exclusions_reject_non_roles(title):
    """Interns and new-grad programmes contain the alias but aren't the role."""
    assert role_match(title) is None


@pytest.mark.parametrize("title", [
    "Founding Engineer - Co-op",
    "Founding Engineer Co-Op",
    "Founding Engineer, Coop",
])
def test_exclusions_survive_punctuation_normalization(title):
    """Regression: an exclusion written "co-op" must fire on a title reading
    "Co-op". Both sides are punctuation-flattened before comparison; without
    that, every co-op posting would slip through as a real role."""
    assert role_match(title) is None


@pytest.mark.parametrize("title", [
    "AI Engineer (Intern)",
    "Data Scientist [Internship]",
    "Founding Engineer (co op)",
    "Machine Learning Engineer (New Grad)",
])
def test_exclusions_inside_brackets_still_reject(title):
    """Regression: normalize_title strips bracketed asides to help matching,
    which would also delete the exclusion signal. Exclusions are therefore
    checked BEFORE that stripping — otherwise "AI Engineer (Intern)" would be
    reported as a genuine AI Engineer opening."""
    assert role_match(title) is None


def test_longest_alias_wins():
    """A title matching two roles resolves to the more specific one."""
    # "forward deployed ai engineer" contains both "ai engineer" and
    # "forward deployed ai engineer" -> the longer alias must win.
    assert role_match("Forward Deployed AI Engineer") == "Forward Deployed Engineer"
    # "founding ai engineer" likewise.
    assert role_match("Founding AI Engineer") == "Founding Engineer"


@pytest.mark.parametrize("title,expected", [
    # A plain Solutions Engineer IS the forward deployed role at many startups.
    ("Solutions Engineer", "Forward Deployed Engineer"),
    ("Solutions Engineer, Europe", "Forward Deployed Engineer"),
    # ...but the sales-side variants are a different job entirely.
    ("Partner Solutions Engineer, EMEA", None),
    ("Pre-Sales Solutions Engineer", None),
    ("Sales Engineer", None),
])
def test_solutions_engineer_excludes_sales_variants(title, expected):
    """Observed on Vercel's live board: "Partner Solutions Engineer, EMEA" was
    matching as an FDE role. Quota-carrying sales roles don't belong in the
    digest, but the bare title is kept because it is often a real FDE post."""
    assert role_match(title) == expected


def test_word_boundaries_prevent_substring_false_positives():
    assert role_match("FDENT Specialist") is None      # 'fde' inside a word
    assert role_match("Database Engineer") is None     # 'data' inside 'database'


def test_custom_matchers_override_defaults():
    custom = {"Platform Engineer": ["platform engineer"]}
    assert role_match("Platform Engineer", custom) == "Platform Engineer"
    # A default role is NOT matched when custom matchers are supplied.
    assert role_match("AI Engineer", custom) is None


def test_every_default_role_matches_its_own_name():
    """Guards against an alias list that forgets its own canonical title."""
    for role in DEFAULT_ROLE_MATCHERS:
        assert role_match(role) == role, role


# ---------------------------------------------------------------------------
# dedup_key
# ---------------------------------------------------------------------------

def test_dedup_key_prefers_external_id():
    key = dedup_key(posting(external_id="12345", source="greenhouse"))
    assert key == "greenhouse:12345"


def test_dedup_key_is_stable_across_title_edits_when_id_present():
    """An ATS id survives the company rewording the title."""
    a = dedup_key(posting(external_id="7", title="AI Engineer", source="lever"))
    b = dedup_key(posting(external_id="7", title="Senior AI Engineer", source="lever"))
    assert a == b


def test_dedup_key_hash_is_stable_across_cosmetic_changes():
    """Without an id, cosmetic title noise must not create a phantom new role."""
    a = dedup_key(posting(title="Senior AI Engineer II (REQ-4821)"))
    b = dedup_key(posting(title="Senior AI Engineer  II"))
    assert a == b


def test_dedup_key_ignores_query_string():
    """Tracking params change per visit; the path is what identifies the role."""
    a = dedup_key(posting(url="https://x/jobs/1?utm_source=a&ref=1"))
    b = dedup_key(posting(url="https://x/jobs/1?utm_source=b"))
    assert a == b


def test_dedup_key_distinguishes_genuinely_different_roles():
    a = dedup_key(posting(title="AI Engineer", url="https://x/jobs/1"))
    b = dedup_key(posting(title="Data Scientist", url="https://x/jobs/2"))
    assert a != b


def test_dedup_key_distinguishes_same_title_different_location():
    """One role open in two cities is two postings."""
    a = dedup_key(posting(title="AI Engineer", location="NYC", url="https://x/jobs/1"))
    b = dedup_key(posting(title="AI Engineer", location="London", url="https://x/jobs/2"))
    assert a != b


def test_dedup_key_handles_malformed_url():
    assert dedup_key(posting(url="")).startswith("h:")


# ---------------------------------------------------------------------------
# Head anchoring — the fix for false positives seen on the live Amazon board
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title", [
    # Both of these were WRONGLY matched before head anchoring: the team names
    # "Open Data Analytics" and "AWS Applied AI Solutions" contain our aliases,
    # but the job itself is a Software Development Engineer role.
    "Software Development Engineer, Open Data Analytics - Engines",
    "Software Dev Engineer II, AWS Applied AI Solutions",
    "Software Engineer, Machine Learning Platform",
    "Senior SDE, Data Science Tools",
    "Product Manager, Data Science",
    "Technical Program Manager, AI Engineering",
])
def test_team_names_do_not_create_false_matches(title):
    """A role is decided by the HEAD of the title, not by the team it sits in."""
    assert role_match(title) is None


@pytest.mark.parametrize("title,expected", [
    ("Applied Scientist - Machine Learning, Amazon Transportation", "Data Scientist"),
    ("Data Scientist II, Amazon Fulfillment Technology", "Data Scientist"),
    ("Applied Scientist II, AWS Agentic AI", "Data Scientist"),
    ("Data Analyst, Retail Ops", "Data Analyst"),
    ("AI Engineer, Platform", "AI Engineer"),
    ("Machine Learning Engineer - Ranking, Search", "AI Engineer"),
])
def test_genuine_roles_still_match_with_team_suffixes(title, expected):
    assert role_match(title) == expected


def test_specific_alias_beats_a_disqualifier():
    """"software engineer" is disqualifying, but "founding software engineer"
    is longer AND a role we want — the more specific phrase must win."""
    assert role_match("Founding Software Engineer") == "Founding Engineer"
    assert role_match("Software Engineer") is None


@pytest.mark.parametrize("title,head", [
    ("Applied Scientist II, AWS Agentic AI", "applied scientist"),
    ("Data Scientist - Machine Learning, Transport", "data scientist"),
    ("AI Engineer (Remote)", "ai engineer"),
    ("Data Analyst | Retail", "data analyst"),
])
def test_role_head_extracts_the_role_part(title, head):
    assert role_head(title) == head
