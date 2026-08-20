"""Parse a country code out of a posting's free-text location.

Location strings arrive in five mutually-incompatible shapes across our sources
(counts from a live 658-posting scan):

    296  "US, WA, Seattle"                    ISO country first  (Amazon)
    317  "Tokyo, Japan" / "Seattle, Washington"  country or state last (Databricks)
    203  "San Francisco"                      bare city, NO country (OpenAI, Perplexity)
     47  "San Francisco, CA, US; Seattle, WA, US"  several locations at once (Pinterest)

Two traps this has to avoid:

* "CA" means CANADA in Amazon's leading-ISO format but CALIFORNIA in
  "Philadelphia, Pennsylvania"-style strings. Position decides, never the token
  alone.
* A third of postings name no country whatsoever. Those resolve to None, and
  None is treated as KEEP by the scrape filter — a parser that cannot identify a
  location must never be the reason a real job is discarded.
"""

from __future__ import annotations

import re

# ISO-3166 alpha-2 for the countries that actually appear on these boards, plus
# the spelled-out names and common aliases.
_COUNTRY_NAMES: dict[str, str] = {
    "united states": "US", "united states of america": "US", "usa": "US", "u.s.": "US",
    "u.s.a.": "US", "america": "US",
    "canada": "CA",
    "united kingdom": "GB", "uk": "GB", "u.k.": "GB", "england": "GB",
    "scotland": "GB", "wales": "GB", "northern ireland": "GB", "great britain": "GB",
    "ireland": "IE", "germany": "DE", "france": "FR", "spain": "ES", "portugal": "PT",
    "italy": "IT", "netherlands": "NL", "belgium": "BE", "switzerland": "CH",
    "austria": "AT", "sweden": "SE", "norway": "NO", "denmark": "DK", "finland": "FI",
    "poland": "PL", "czechia": "CZ", "czech republic": "CZ", "romania": "RO",
    "serbia": "RS", "greece": "GR", "turkey": "TR", "israel": "IL",
    "india": "IN", "china": "CN", "japan": "JP", "south korea": "KR", "korea": "KR",
    "singapore": "SG", "australia": "AU", "new zealand": "NZ", "brazil": "BR",
    "mexico": "MX", "argentina": "AR", "chile": "CL", "colombia": "CO",
    "united arab emirates": "AE", "uae": "AE", "saudi arabia": "SA",
    "south africa": "ZA", "nigeria": "NG", "kenya": "KE", "egypt": "EG",
    "hong kong": "HK", "taiwan": "TW", "thailand": "TH", "vietnam": "VN",
    "philippines": "PH", "indonesia": "ID", "malaysia": "MY", "pakistan": "PK",
    "lithuania": "LT", "latvia": "LV", "estonia": "EE", "ukraine": "UA",
    "hungary": "HU", "bulgaria": "BG", "croatia": "HR", "slovakia": "SK",
    "luxembourg": "LU", "iceland": "IS", "costa rica": "CR", "uruguay": "UY",
    "peru": "PE", "panama": "PA",
}

# US states, to stop "Seattle, Washington" reading as a country and to resolve
# US cities that name only a state.
_US_STATES = {
    "alabama","alaska","arizona","arkansas","california","colorado","connecticut",
    "delaware","florida","georgia","hawaii","idaho","illinois","indiana","iowa",
    "kansas","kentucky","louisiana","maine","maryland","massachusetts","michigan",
    "minnesota","mississippi","missouri","montana","nebraska","nevada",
    "new hampshire","new jersey","new mexico","new york","north carolina",
    "north dakota","ohio","oklahoma","oregon","pennsylvania","rhode island",
    "south carolina","south dakota","tennessee","texas","utah","vermont",
    "virginia","washington","west virginia","wisconsin","wyoming",
    "district of columbia","washington dc","washington d.c.",
}

# Bare-city fallback — the cities that appear WITHOUT any country on these
# boards. Deliberately small and high-confidence; an unlisted city stays None
# rather than being guessed at.
_CITY_COUNTRY: dict[str, str] = {
    "san francisco": "US", "new york": "US", "new york city": "US", "nyc": "US",
    "seattle": "US", "boston": "US", "chicago": "US", "austin": "US",
    "los angeles": "US", "san jose": "US", "palo alto": "US", "mountain view": "US",
    "sunnyvale": "US", "santa clara": "US", "bellevue": "US", "denver": "US",
    "atlanta": "US", "miami": "US", "washington": "US", "san diego": "US",
    "philadelphia": "US", "portland": "US", "nashville": "US", "detroit": "US",
    "menlo park": "US", "south san francisco": "US", "redmond": "US",
    "cupertino": "US", "arlington": "US", "east palo alto": "US", "irvine": "US",
    "dallas": "US", "houston": "US", "phoenix": "US", "minneapolis": "US",
    "toronto": "CA", "vancouver": "CA", "montreal": "CA", "ottawa": "CA",
    "waterloo": "CA", "calgary": "CA",
    "london": "GB", "manchester": "GB", "edinburgh": "GB", "cambridge": "GB",
    "dublin": "IE", "berlin": "DE", "munich": "DE", "hamburg": "DE",
    "paris": "FR", "amsterdam": "NL", "zurich": "CH", "madrid": "ES",
    "barcelona": "ES", "lisbon": "PT", "milan": "IT", "rome": "IT",
    "stockholm": "SE", "oslo": "NO", "copenhagen": "DK", "helsinki": "FI",
    "warsaw": "PL", "prague": "CZ", "belgrade": "RS", "bucharest": "RO",
    "vilnius": "LT", "tel aviv": "IL",
    "bangalore": "IN", "bengaluru": "IN", "hyderabad": "IN", "mumbai": "IN",
    "delhi": "IN", "new delhi": "IN", "chennai": "IN", "pune": "IN", "gurgaon": "IN",
    "tokyo": "JP", "osaka": "JP", "seoul": "KR", "beijing": "CN", "shanghai": "CN",
    "shenzhen": "CN", "singapore": "SG", "hong kong": "HK", "taipei": "TW",
    "sydney": "AU", "melbourne": "AU", "auckland": "NZ",
    "sao paulo": "BR", "são paulo": "BR", "mexico city": "MX",
    "dubai": "AE", "abu dhabi": "AE", "tel-aviv": "IL",
}

# Every code parse_country can return — used to validate user-supplied filters,
# since a well-formed but unreal code like "XX" would otherwise match nothing.
KNOWN_COUNTRY_CODES: frozenset[str] = frozenset(_COUNTRY_NAMES.values())

_ISO_RE = re.compile(r"^([A-Z]{2})\b")

# US state abbreviations, only ever consulted in a TRAILING segment. A leading
# two-letter token is handled as an ISO country code above, which is what keeps
# "CA, BC, Vancouver" (Canada) apart from "Menlo Park, CA" (California).
_US_STATE_ABBR = {
    "al","ak","az","ar","ca","co","ct","de","fl","ga","hi","id","il","in","ia",
    "ks","ky","la","me","md","ma","mi","mn","ms","mo","mt","ne","nv","nh","nj",
    "nm","ny","nc","nd","oh","ok","or","pa","ri","sc","sd","tn","tx","ut","vt",
    "va","wa","wv","wi","wy","dc",
}

# Region phrasings that name a country without a city ("Central - United States").
_REGION_COUNTRY_RE = re.compile(
    r"\b(united states|usa|u\.s\.a?\.?|canada|united kingdom|uk)\b", re.I
)
_REMOTE_RE = re.compile(r"\bremote\b", re.I)


def _clean_part(part: str) -> str:
    """Strip decoration a location segment picks up ("(HQ)", "Remote - ")."""
    text = re.sub(r"\([^)]*\)", " ", part or "")
    text = re.sub(r"\b(remote|hybrid|onsite|on-site)\b", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip(" ,-–—").strip().lower()


def parse_country(location: str | None) -> str | None:
    """Best-effort ISO-3166 alpha-2 country for a location string.

    Returns None when no country can be identified with confidence — which the
    scrape filter treats as KEEP, never as a reason to discard.
    """
    if not location or not isinstance(location, str):
        return None

    # Multi-location strings ("SF, CA, US; Seattle, WA, US"): take the first,
    # which is the primary posting location on every board seen so far.
    primary = location.split(";")[0]

    # 1. Leading ISO code — Amazon's format. Position is what disambiguates
    #    "CA" (Canada here) from "CA" as California in a trailing position.
    stripped = primary.strip()

    # "Central - United States": read a spelled-out country before _clean_part
    # strips the "remote" token and the segment around it.
    region = _REGION_COUNTRY_RE.search(stripped)
    if region:
        return _COUNTRY_NAMES.get(region.group(1).lower().rstrip("."), "US")

    match = _ISO_RE.match(stripped)
    if match:
        code = match.group(1)
        # Guard: "US, WA, Seattle" is a country prefix, but a string starting
        # with a state abbreviation only counts when more segments follow.
        known_codes = set(_COUNTRY_NAMES.values())
        if code in known_codes:
            # With a comma it is Amazon's "US, WA, Seattle" form. Without one it
            # is a bare country ("US", "US - Remote") — still unambiguous,
            # because a trailing state abbreviation never leads the string.
            if "," in stripped or len(stripped) <= 3 or not stripped[2:3].isalpha():
                return code

    parts = [_clean_part(p) for p in primary.split(",")]
    parts = [p for p in parts if p]
    if not parts:
        return None

    # 2. Explicit country name anywhere in the string (usually last).
    for part in reversed(parts):
        if part in _COUNTRY_NAMES:
            return _COUNTRY_NAMES[part]

    # 3. A US state name implies the US ("Seattle, Washington").
    for part in reversed(parts):
        if part in _US_STATES:
            return "US"

    # 4. Bare city fallback ("San Francisco").
    for part in parts:
        if part in _CITY_COUNTRY:
            return _CITY_COUNTRY[part]

    # 5. Trailing US state abbreviation ("Menlo Park, CA"). Only valid here:
    #    a LEADING two-letter token was already read as a country code above.
    if len(parts) >= 2 and parts[-1] in _US_STATE_ABBR:
        return "US"

    # 6. A country named inside a region phrase ("Central - United States",
    #    "US - Remote") that the comma split doesn't isolate.
    match = _REGION_COUNTRY_RE.search(primary)
    if match:
        return _COUNTRY_NAMES.get(match.group(1).lower().rstrip("."), "US")

    return None


def is_remote(location: str | None) -> bool:
    """True when the location advertises remote work."""
    return bool(location and _REMOTE_RE.search(location))
