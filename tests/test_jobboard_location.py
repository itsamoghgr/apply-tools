"""Country parsing from free-text locations.

Location strings arrive in five incompatible formats across sources. The tests
that matter most are the ambiguity traps and the fail-open guarantee: an
unresolvable location returns None, and the scrape filter treats None as KEEP.
"""

from __future__ import annotations

import pytest

from agent_server.jobboard.location import is_remote, parse_country


@pytest.mark.parametrize("location,expected", [
    # Amazon: leading ISO country code.
    ("US, WA, Seattle", "US"),
    ("CA, BC, Vancouver", "CA"),
    ("JP, 13, Tokyo", "JP"),
    ("IN, KA, Bengaluru", "IN"),
    # Databricks: country or US state last.
    ("Tokyo, Japan", "JP"),
    ("Seattle, Washington", "US"),
    ("Philadelphia, Pennsylvania", "US"),
    ("London, UK", "GB"),
    # Bare city (OpenAI, Perplexity).
    ("San Francisco", "US"),
    ("Belgrade", "RS"),
    ("Bengaluru", "IN"),
    # Decorated / multi-location / region phrasings.
    ("New York, NY (HQ)", "US"),
    ("San Francisco, CA, US; Seattle, WA, US", "US"),
    ("Remote - India", "IN"),
    ("Central - United States", "US"),
    ("US - Remote", "US"),
    ("Menlo Park, CA", "US"),
])
def test_parses_real_world_formats(location, expected):
    assert parse_country(location) == expected


def test_leading_ca_is_canada_trailing_ca_is_california():
    """THE ambiguity that makes naive token matching wrong: "CA" is Canada in
    Amazon's leading-ISO format but California in a trailing position."""
    assert parse_country("CA, BC, Vancouver") == "CA"
    assert parse_country("Menlo Park, CA") == "US"
    assert parse_country("US, CA, Sunnyvale") == "US"


@pytest.mark.parametrize("location", [
    "Remote", "n/a", "LOCATION", "", None, "Mars Base One",
])
def test_unresolvable_locations_return_none(location):
    """None means "unknown", and the scrape filter reads unknown as KEEP —
    a parser miss must never silently delete a real job."""
    assert parse_country(location) is None


def test_multi_location_takes_the_first():
    assert parse_country("Menlo Park, CA; London, UK") == "US"


def test_is_remote():
    assert is_remote("US - Remote") is True
    assert is_remote("Remote - India") is True
    assert is_remote("Seattle, WA") is False
    assert is_remote(None) is False
