"""Ashby job-board adapter.

    GET api.ashbyhq.com/posting-api/job-board/<slug>
    -> {"jobs": [{id, title, jobUrl, location, publishedAt,
                  isListed, department, team, ...}]}

Public, unauthenticated, whole board in one call. Verified against linear.

`isListed` marks whether Ashby is publicly showing the posting; unlisted rows are
skipped so we never report a role a visitor cannot see.
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

SOURCE = "ashby"
API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def fetch(slug: str) -> list[RawPosting]:
    data = get_json(API.format(slug=slug))
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise AdapterError("ashby: expected {'jobs': [...]}")

    out: list[RawPosting] = []
    for job in data["jobs"]:
        if not isinstance(job, dict):
            continue
        # Only skip when explicitly false — a missing flag means "listed".
        if job.get("isListed") is False:
            continue
        title = clean(job.get("title"))
        url = clean(job.get("jobUrl")) or clean(job.get("applyUrl"))
        if not (title and url):
            continue
        out.append(
            RawPosting(
                external_id=clean(job.get("id")),
                title=title,
                location=clean(job.get("location")),
                url=url,
                posted_at=parse_iso(job.get("publishedAt")),
                source=SOURCE,
                min_years=extract_min_years(
                    job.get("descriptionPlain"), job.get("descriptionHtml")
                ),
            )
        )
    return out
