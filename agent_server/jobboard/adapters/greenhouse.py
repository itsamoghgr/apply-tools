"""Greenhouse job-board adapter.

    GET boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=false
    -> {"jobs": [{id, title, absolute_url, location:{name},
                  first_published, updated_at, ...}]}

Public, unauthenticated, whole board in one call. `content=false` drops the job
descriptions, which we don't need and which multiply the payload size.

Date choice: `first_published` is when the role went live; `updated_at` moves on
any edit (a whole board can share one updated_at after a bulk change). We want
the former. Verified present on 84/84 Vercel postings.
"""

from __future__ import annotations

from agent_server.jobboard.adapters.base import (
    AdapterError,
    RawPosting,
    clean,
    get_json,
    parse_iso,
)
from agent_server.jobboard.experience import extract_min_years

SOURCE = "greenhouse"
API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


def fetch(slug: str) -> list[RawPosting]:
    data = get_json(API.format(slug=slug), params={"content": "true"})
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise AdapterError("greenhouse: expected {'jobs': [...]}")

    out: list[RawPosting] = []
    for job in data["jobs"]:
        if not isinstance(job, dict):
            continue
        title = clean(job.get("title"))
        url = clean(job.get("absolute_url"))
        if not (title and url):
            continue
        loc = job.get("location")
        out.append(
            RawPosting(
                external_id=str(job["id"]) if job.get("id") is not None else None,
                title=title,
                location=clean(loc.get("name")) if isinstance(loc, dict) else None,
                url=url,
                posted_at=parse_iso(job.get("first_published"))
                or parse_iso(job.get("updated_at")),
                source=SOURCE,
                min_years=extract_min_years(job.get("content")),
            )
        )
    return out
