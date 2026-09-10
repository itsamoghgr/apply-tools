"""Extract a minimum years-of-experience requirement from a job description.

Deterministic regex, NOT an LLM. The JD text already arrives in the same API
call that returns the posting (Greenhouse `content`, Ashby `descriptionPlain`,
Amazon `basic_qualifications`), so this costs nothing extra per cycle — which
matters when the alternative is one model call per posting across thousands.

Design decisions worth stating:

* Returns the MINIMUM stated figure. A JD saying "5+ years preferred, 3+
  required" is a 3-year job; taking the max would filter it out of your results
  wrongly.
* Returns None when nothing is stated, and None is never treated as 0 or as
  "junior". Roughly a third of postings genuinely don't state a number, and
  inventing one would silently hide real matches behind a filter.
* Caps at 20 years: past that the match is almost always a stray number
  ("20 years of company history"), not a requirement.
"""

from __future__ import annotations

import html
import re

# Ordered loosest-last. Each pattern captures the year count in group 1.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "5+ years", "5 + yrs"
    re.compile(r"(\d{1,2})\s*\+\s*(?:years?|yrs?)\b"),
    # "at least 5 years", "minimum of 5 years", "min. 5 yrs"
    re.compile(r"(?:at least|minimum(?:\s+of)?|min\.?)\s*(\d{1,2})\s*(?:years?|yrs?)\b"),
    # "3-5 years", "3 to 5 years" -> take the LOWER bound (group 1)
    re.compile(r"(\d{1,2})\s*(?:-|–|—|to)\s*\d{1,2}\s*(?:years?|yrs?)\b"),
    # "5 years of experience", "5 years relevant experience"
    re.compile(
        r"(\d{1,2})\s*(?:years?|yrs?)\s+(?:of\s+)?"
        r"(?:relevant\s+|professional\s+|industry\s+|related\s+|work\s+)?experience"
    ),
)

_TAG = re.compile(r"<[^>]+>")
_MAX_YEARS = 20


def _plain(text: str) -> str:
    """HTML -> lowercase plain text. Greenhouse double-escapes, hence two passes."""
    if not text:
        return ""
    unescaped = html.unescape(html.unescape(text))
    return _TAG.sub(" ", unescaped).lower()


def extract_min_years(*sources: str | None) -> int | None:
    """Smallest stated years-of-experience across the given text blocks.

    Accepts several sources (e.g. a qualifications field plus the full
    description) and considers all of them, since adapters expose different
    fields. Returns None when no source states a requirement.
    """
    best: int | None = None
    for source in sources:
        text = _plain(source or "")
        if not text:
            continue
        for pattern in _PATTERNS:
            for match in pattern.finditer(text):
                try:
                    years = int(match.group(1))
                except (TypeError, ValueError):
                    continue
                if 0 <= years <= _MAX_YEARS:
                    best = years if best is None else min(best, years)
    return best


# Display bands for the UI filter. Chosen to match how job levels actually
# cluster rather than at even intervals.
EXPERIENCE_BANDS: tuple[tuple[str, str, int | None, int | None], ...] = (
    ("entry", "0-2 years", 0, 2),
    ("mid", "3-5 years", 3, 5),
    ("senior", "6-9 years", 6, 9),
    ("staff", "10+ years", 10, None),
)


def band_for(min_years: int | None) -> str | None:
    """The band key a posting falls into, or None when unstated."""
    if min_years is None:
        return None
    for key, _label, low, high in EXPERIENCE_BANDS:
        if min_years >= low and (high is None or min_years <= high):
            return key
    return None
