"""Role matching and dedup-key derivation.

Two jobs, both deliberately deterministic (no LLM):

1. ROLE MATCHING. Exact string equality would miss most real titles — a Forward
   Deployed Engineer role ships as "Forward Deployed AI Engineer", "FDE", or
   "Forward-Deployed Engineer II". So each target role owns a list of aliases,
   matched as substrings against a normalised title. LONGEST alias wins, so a
   title matching both "engineer" and "founding engineer" resolves to the more
   specific one.

2. DEDUP KEYS. The monitor decides novelty against what is already stored, so a
   posting needs a key that is stable across cycles: the ATS external id when
   the source gives one, else a hash of the normalised title + location + URL
   path. See docs/job_board_backend.md §3.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

from agent_server.jobboard.adapters.base import RawPosting

# The user's four target roles, with the aliases each realistically appears
# under. Seeded into the platform Setting table (key 'jobboard.roleMatchers') on
# first use, after which it is editable in the UI without a deploy.
DEFAULT_ROLE_MATCHERS: dict[str, list[str]] = {
    "Data Scientist": [
        "data scientist",
        "data science",
        "applied scientist",
        "research scientist",
        "quantitative researcher",
    ],
    "Data Analyst": [
        "data analyst",
        "data analytics",
        "analytics engineer",
    ],
    "AI Engineer": [
        "ai engineer",
        "a.i. engineer",
        "artificial intelligence engineer",
        "ml engineer",
        "machine learning engineer",
        "applied ai",
        "applied ml",
        "applied machine learning",
        "genai engineer",
        "gen ai engineer",
        "generative ai engineer",
        "llm engineer",
        "ai/ml engineer",
    ],
    "Founding Engineer": [
        "founding engineer",
        "founding software engineer",
        "founding ai engineer",
        "founding full stack engineer",
        "founding backend engineer",
        "member of technical staff",
    ],
    "Forward Deployed Engineer": [
        "forward deployed engineer",
        "forward-deployed engineer",
        "forward deployed ai engineer",
        "forward deployed software engineer",
        "fde",
        "deployment engineer",
        "deployment strategist",
        "solutions engineer",
        "field engineer",
    ],
}

# Titles that merely CONTAIN an alias but are not the role: internships,
# early-career programmes, and the like. A hit rejects the posting.
#
# These are matched against an ALREADY-NORMALISED title, so they must be written
# in normalised form themselves: normalize_title turns "Co-op" into "co op", so
# an entry of "co-op" here would silently never fire. _normalize_alias is applied
# below to guarantee that regardless of how an entry is written.
_EXCLUSIONS: tuple[str, ...] = (
    "intern",
    "internship",
    "co op",
    "coop",
    "apprentice",
    "working student",
    "new grad",
    "graduate program",
    # Sales-adjacent qualifiers on "solutions engineer". That alias is kept
    # because at smaller companies a plain "Solutions Engineer" IS the forward
    # deployed role — but "Partner/Pre-Sales Solutions Engineer" is a sales
    # function, and matching it would put quota-carrying roles in the digest.
    "partner solutions engineer",
    "pre sales",
    "presales",
    "sales engineer",
    "sales solutions engineer",
)

# Roles that are fundamentally a DIFFERENT job. If one of these is the head of
# the title, the posting is rejected regardless of what the team is called —
# "Software Development Engineer, Open Data Analytics" is an SDE job, not a Data
# Analyst job. Matched against the head only.
_HEAD_DISQUALIFIERS: tuple[str, ...] = (
    "software development engineer",
    "software dev engineer",
    "software engineer",
    "sde",
    "frontend engineer",
    "front end engineer",
    "backend engineer",
    "back end engineer",
    "full stack engineer",
    "fullstack engineer",
    "devops engineer",
    "security engineer",
    "network engineer",
    "systems engineer",
    "hardware engineer",
    "product manager",
    "program manager",
    "project manager",
    "technical program manager",
    "account executive",
    "recruiter",
    "designer",
)

# Stripped from a title before matching: req ids, level suffixes, bracketed
# location/employment notes. Keeps "Senior AI Engineer II (REQ-4821), Remote"
# and "Senior AI Engineer" from reading as unrelated strings.
_REQ_ID = re.compile(r"\b(?:req|job|jr|requisition)[\s#:_-]*\d[\w-]*", re.I)
_BRACKETED = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_LEVEL_SUFFIX = re.compile(r"\b(?:i{1,3}|iv|v|vi{0,3}|[1-9])\b\s*$", re.I)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    """Lowercase and strip the noise that varies between listings of one role."""
    text = (title or "").lower()
    text = _BRACKETED.sub(" ", text)
    text = _REQ_ID.sub(" ", text)
    text = _NON_ALNUM.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = _LEVEL_SUFFIX.sub("", text).strip()
    return text


def _normalize_alias(alias: str) -> str:
    text = _NON_ALNUM.sub(" ", (alias or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def role_head(title: str) -> str:
    """The ROLE part of a title — everything before the first team/org separator.

    Job titles are overwhelmingly "<role>, <team>" or "<role> - <team>":

        "Software Development Engineer, Open Data Analytics"  -> the role is SDE
        "Applied Scientist II, AWS Agentic AI"                -> Applied Scientist

    Matching the whole string is what produced the false positives that started
    this: "Open Data Analytics" made an SDE look like a Data Analyst, and "AWS
    Applied AI Solutions" made another SDE look like an AI Engineer. Both are
    team names, not roles. Anchoring on the head fixes that class of error
    outright.
    """
    text = (title or "").strip()
    # Split on the first separator that introduces a team/org/location.
    for sep in (",", " - ", " -- ", " – ", " — ", "(", "|"):
        idx = text.find(sep)
        if idx > 0:
            text = text[:idx]
    return normalize_title(text)


def role_match(
    title: str, matchers: dict[str, list[str]] | None = None
) -> str | None:
    """Return the target role `title` matches, or None.

    Matching is deliberately STRICT: an alias must appear in the title's HEAD
    (the part before the first comma/dash), not anywhere in the string. A team
    called "Open Data Analytics" therefore cannot turn a Software Development
    Engineer into a Data Analyst.

    Longest alias wins, so a title matching both "engineer" and "founding
    engineer" resolves to the more specific role.
    """
    matchers = matchers or DEFAULT_ROLE_MATCHERS

    # Exclusions run against the FULL title (lightly normalised), because
    # "Intern"/"Co-op" often sit in the tail or in brackets that the head split
    # and normalize_title would discard.
    for_exclusion = _normalize_alias(title)
    for term in _EXCLUSIONS:
        needle = _normalize_alias(term)
        if needle and re.search(rf"\b{re.escape(needle)}\b", for_exclusion):
            return None

    head = role_head(title)
    if not head:
        return None

    # Best matching alias in the head, longest wins.
    best_role: str | None = None
    best_len = 0
    for role, aliases in matchers.items():
        for alias in aliases or []:
            needle = _normalize_alias(alias)
            if not needle:
                continue
            if re.search(rf"\b{re.escape(needle)}\b", head) and len(needle) > best_len:
                best_role, best_len = role, len(needle)

    # A head that is fundamentally a DIFFERENT job is rejected — but only when
    # the disqualifier is at least as specific as the alias that matched.
    # "Founding Software Engineer" contains "software engineer", yet "founding
    # software engineer" is longer and IS a role we want; without this
    # comparison the disqualifier would veto a genuine hit.
    for term in _HEAD_DISQUALIFIERS:
        needle = _normalize_alias(term)
        if re.search(rf"\b{re.escape(needle)}\b", head) and len(needle) >= best_len:
            return None

    return best_role


def dedup_key(posting: RawPosting) -> str:
    """A key that is stable for one posting across monitor cycles.

    Prefers the ATS external id — authoritative, and immune to a company editing
    a title. Falls back to a hash of normalised title + location + URL path
    (never the full URL, whose query string can carry per-visit tracking params).
    """
    if posting.external_id:
        return f"{posting.source}:{posting.external_id}"

    try:
        path = urlparse(posting.url).path.rstrip("/")
    except ValueError:
        path = ""
    basis = "|".join(
        [
            normalize_title(posting.title),
            _normalize_alias(posting.location or ""),
            path,
        ]
    )
    return "h:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:24]
