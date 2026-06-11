"""Tests for the shared domain normalizer (CONTRACTS §3)."""

from __future__ import annotations

import pytest

from agent_server.stages.normalize import NON_COMPANY_HOSTS, normalize_domain


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("acme.com", "acme.com"),
        ("ACME.com", "acme.com"),
        ("http://www.Acme.com/x?y=1", "acme.com"),
        ("https://acme.com", "acme.com"),
        ("www.acme.com", "acme.com"),
        ("acme.com:8080/path", "acme.com"),
        ("blog.acme.com", "acme.com"),
        # multi-part TLDs collapse to the registrable root
        ("blog.acme.co.uk", "acme.co.uk"),
        ("acme.co.uk", "acme.co.uk"),
        ("sub.deep.acme.com.au", "acme.com.au"),
    ],
)
def test_normalizes_to_root_domain(raw, expected):
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "linkedin.com",
        "https://www.linkedin.com/in/janedoe",
        "twitter.com",
        "x.com",
        "facebook.com",
        "medium.com",
        "github.com",
        "youtube.com",
        "crunchbase.com",
        "producthunt.com",
    ],
)
def test_non_company_hosts_return_none(raw):
    assert normalize_domain(raw) is None


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "not a domain", "http://", "justtext", None],
)
def test_junk_returns_none(raw):
    assert normalize_domain(raw) is None  # type: ignore[arg-type]


def test_blocklist_is_a_set_of_bare_hosts():
    assert "linkedin.com" in NON_COMPANY_HOSTS
    # blocklist holds bare registrable hosts, lowercased
    assert all(h == h.lower() for h in NON_COMPANY_HOSTS)
