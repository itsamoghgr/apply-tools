"""SmartRecruiters job-board adapter.

    GET api.smartrecruiters.com/v1/companies/<slug>/postings?limit=100&offset=N
    -> {"offset": N, "limit": 100, "totalFound": T,
        "content": [{id, name, releasedDate, location:{city,region,country}, ...}]}

The ONE ATS here that genuinely paginates: it answers with an offset/limit/
totalFound envelope and caps a page at 100. The others return a whole board in a
single call. Verified against Visa (totalFound=2).

The posting URL is not in the payload, so it is composed from the documented
public job-ad pattern: jobs.smartrecruiters.com/<slug>/<id>.
"""

from __future__ import annotations

from agent_server.jobboard.adapters.base import (
    AdapterError,
    RawPosting,
    clean,
    get_json,
    parse_iso,
)

SOURCE = "smartrecruiters"
API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
JOB_URL = "https://jobs.smartrecruiters.com/{slug}/{job_id}"

PAGE_SIZE = 100
# Hard stop so a bad totalFound (or a board that keeps growing mid-walk) can
# never spin forever: 50 pages * 100 = 5,000 postings, far beyond any real board.
MAX_PAGES = 50


def _location(job: dict) -> str | None:
    """Flatten {city, region, country} into "City, Region"."""
    loc = job.get("location")
    if not isinstance(loc, dict):
        return None
    parts = [clean(loc.get("city")), clean(loc.get("region")) or clean(loc.get("country"))]
    return ", ".join(p for p in parts if p) or None


def fetch(slug: str) -> list[RawPosting]:
    out: list[RawPosting] = []
    seen_ids: set[str] = set()
    offset = 0

    for _ in range(MAX_PAGES):
        data = get_json(API.format(slug=slug), params={"limit": PAGE_SIZE, "offset": offset})
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            raise AdapterError("smartrecruiters: expected {'content': [...]}")

        page = data["content"]
        for job in page:
            if not isinstance(job, dict):
                continue
            job_id = clean(job.get("id"))
            title = clean(job.get("name"))
            if not title:
                continue
            # Defensive: a shifting board can repeat a row across page boundaries.
            if job_id and job_id in seen_ids:
                continue
            if job_id:
                seen_ids.add(job_id)
            out.append(
                RawPosting(
                    external_id=job_id,
                    title=title,
                    location=_location(job),
                    url=JOB_URL.format(slug=slug, job_id=job_id) if job_id else "",
                    posted_at=parse_iso(job.get("releasedDate")),
                    source=SOURCE,
                )
            )

        offset += PAGE_SIZE
        total = data.get("totalFound")
        # Stop on a short page (authoritative) or once we've covered totalFound.
        if len(page) < PAGE_SIZE:
            break
        if isinstance(total, int) and offset >= total:
            break

    # A posting with no id has no URL either; drop rather than emit a dead link.
    return [p for p in out if p.url]
