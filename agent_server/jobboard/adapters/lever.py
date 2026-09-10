"""Lever job-board adapter.

    GET api.lever.co/v0/postings/<slug>?mode=json
    -> [{id, text, hostedUrl, createdAt (epoch ms),
         categories:{location, allLocations, team, commitment}, ...}]

Public, unauthenticated, whole board in one call — a bare JSON ARRAY, not an
envelope. Note the field names differ from every other ATS: the title is `text`
(not `title`) and the URL is `hostedUrl`. Verified against palantir (307 postings).
"""

from __future__ import annotations

from agent_server.jobboard.adapters.base import (
    AdapterError,
    RawPosting,
    clean,
    get_json,
    parse_epoch_ms,
)
from agent_server.jobboard.experience import extract_min_years

SOURCE = "lever"
API = "https://api.lever.co/v0/postings/{slug}"


def fetch(slug: str) -> list[RawPosting]:
    data = get_json(API.format(slug=slug), params={"mode": "json"})
    # Lever answers a bad slug with {"ok": false, "error": "Document not found"}
    # and HTTP 200, so a non-list body is the only signal that the slug is wrong.
    if not isinstance(data, list):
        detail = ""
        if isinstance(data, dict):
            detail = f": {data.get('error') or data}"
        raise AdapterError(f"lever: expected a JSON array (unknown slug?){detail}")

    out: list[RawPosting] = []
    for job in data:
        if not isinstance(job, dict):
            continue
        title = clean(job.get("text"))
        url = clean(job.get("hostedUrl")) or clean(job.get("applyUrl"))
        if not (title and url):
            continue
        cats = job.get("categories") if isinstance(job.get("categories"), dict) else {}
        out.append(
            RawPosting(
                external_id=clean(job.get("id")),
                title=title,
                location=clean(cats.get("location")),
                url=url,
                posted_at=parse_epoch_ms(job.get("createdAt")),
                source=SOURCE,
                min_years=extract_min_years(
                    job.get("descriptionPlain"), job.get("additionalPlain")
                ),
            )
        )
    return out
