"""Years-of-experience extraction tests.

Deterministic regex over JD text the adapters already receive — no LLM, no extra
request. The behaviour that matters: never guess. A posting that doesn't state a
requirement must return None, because treating "unstated" as 0 would hide real
roles behind an experience filter.
"""

from __future__ import annotations

import pytest

from agent_server.jobboard.experience import (
    EXPERIENCE_BANDS,
    band_for,
    extract_min_years,
)


@pytest.mark.parametrize("text,expected", [
    ("- 3+ years of data scientist experience", 3),
    ("5+ years experience required", 5),
    ("At least 4 years of professional experience", 4),
    ("Minimum of 7 years", 7),
    ("minimum 2 yrs", 2),
    ("3-5 years of relevant experience", 3),      # lower bound
    ("3 to 5 years experience", 3),
    ("8 years of industry experience", 8),
    ("10+ yrs", 10),
])
def test_extracts_stated_requirements(text, expected):
    assert extract_min_years(text) == expected


def test_takes_the_minimum_when_several_are_stated():
    """"5+ preferred, 3+ required" is a 3-year job — taking the max would
    wrongly filter it out of a mid-level search."""
    assert extract_min_years("5+ years preferred, 3+ years required") == 3


@pytest.mark.parametrize("text", [
    "We are a fast-growing team with no stated requirement",
    "",
    None,
    "Founded 25 years ago",          # out of range -> not a requirement
    "Over 30 years of company history",
])
def test_returns_none_when_not_stated(text):
    assert extract_min_years(text) is None


def test_strips_html_and_entities():
    """Greenhouse double-escapes its content field."""
    assert extract_min_years("&lt;p&gt;We require 7+ years&lt;/p&gt;") == 7
    assert extract_min_years("<p>Requires <b>4+ years</b></p>") == 4


def test_reads_across_multiple_sources():
    """Adapters expose different fields; all are considered."""
    assert extract_min_years(None, "requires 6+ years", "") == 6
    assert extract_min_years("8+ years", "3+ years") == 3


def test_ignores_absurd_values():
    assert extract_min_years("50+ years of experience") is None


@pytest.mark.parametrize("years,band", [
    (0, "entry"), (2, "entry"),
    (3, "mid"), (5, "mid"),
    (6, "senior"), (9, "senior"),
    (10, "staff"), (18, "staff"),
    (None, None),
])
def test_band_assignment(years, band):
    assert band_for(years) == band


def test_bands_are_contiguous_and_ordered():
    """No gaps between bands — every year value lands somewhere."""
    for years in range(0, 21):
        assert band_for(years) is not None, years
    lows = [low for _k, _l, low, _h in EXPERIENCE_BANDS]
    assert lows == sorted(lows)
