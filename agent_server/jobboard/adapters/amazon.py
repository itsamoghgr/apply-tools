"""Amazon Jobs adapter (amazon.jobs — also serves AWS, Twitch, Audible, Zoox).

    GET www.amazon.jobs/en/search.json?base_query=<q>&result_limit=100&sort=recent
    -> {"hits": N, "jobs": [{id_icims, title, job_path, location, posted_date, ...}]}

Amazon does not use a third-party ATS, but its careers site is backed by a
public JSON search endpoint — so this gets the same exactness as the Greenhouse
and Lever adapters rather than falling back to an LLM scrape.

Unlike an ATS board there is no "whole board" call: 6,900+ open roles make a full
listing impractical, so this QUERIES for the user's target roles and merges the
results. That is a deliberate difference from the other adapters, and it means
the role terms are baked into the fetch rather than applied afterwards.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent_server.jobboard.adapters.base import (
    AdapterError,
    RawPosting,
    clean,
    get_json,
)
from agent_server.jobboard.experience import extract_min_years

SOURCE = "amazon"
API = "https://www.amazon.jobs/en/search.json"
JOB_URL = "https://www.amazon.jobs{path}"

# One query per target role. Amazon's search is keyword-based, so asking for the
# roles we care about is far cheaper than paging 6,900 jobs and filtering after.
DEFAULT_QUERIES = (
    "data scientist",
    "data analyst",
    "machine learning engineer",
    "applied scientist",
    "software development engineer",
)

PAGE_SIZE = 100
# Amazon caps result_limit; keep well inside it and bound total work per cycle.
MAX_PER_QUERY = 200


def _parse_posted(value: str | None) -> datetime | None:
    """Parse Amazon's human date ("August 19, 2026") to aware UTC.

    Amazon returns a formatted date rather than ISO, so fromisoformat is no use
    here. An unparseable value degrades to None, which is safe: novelty is
    decided by the dedup key, never by this field.
    """
    if not value or not isinstance(value, str):
        return None
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _to_posting(job: dict) -> RawPosting | None:
    title = clean(job.get("title"))
    path = clean(job.get("job_path"))
    if not (title and path):
        return None
    return RawPosting(
        # id_icims is Amazon's stable requisition id and survives title edits.
        external_id=clean(job.get("id_icims")) or clean(str(job.get("id") or "")),
        title=title,
        location=clean(job.get("location")) or clean(job.get("city")),
        url=JOB_URL.format(path=path),
        posted_at=_parse_posted(job.get("posted_date")),
        source=SOURCE,
        min_years=extract_min_years(
            job.get("basic_qualifications"), job.get("description_short")
        ),
    )


def fetch(slug: str = "", *, queries: tuple[str, ...] = DEFAULT_QUERIES) -> list[RawPosting]:
    """Search Amazon Jobs for the target roles and return merged postings.

    `slug` is accepted for signature-compatibility with the ATS adapters and is
    ignored — amazon.jobs is a single global board, not a per-company one.

    A query that fails is skipped rather than aborting the whole fetch: partial
    results beat none, and the next cycle retries.
    """
    seen: set[str] = set()
    out: list[RawPosting] = []
    errors = 0

    for query in queries:
        offset = 0
        while offset < MAX_PER_QUERY:
            try:
                data = get_json(
                    API,
                    params={
                        "base_query": query,
                        "result_limit": PAGE_SIZE,
                        "offset": offset,
                        "sort": "recent",
                    },
                )
            except AdapterError:
                errors += 1
                break

            if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
                errors += 1
                break

            jobs = data["jobs"]
            if not jobs:
                break

            for job in jobs:
                if not isinstance(job, dict):
                    continue
                posting = _to_posting(job)
                if posting is None:
                    continue
                # One role can match several queries; keep the first sighting.
                key = posting.external_id or posting.url
                if key in seen:
                    continue
                seen.add(key)
                out.append(posting)

            if len(jobs) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

    if not out and errors:
        raise AdapterError(f"amazon: all {errors} queries failed")
    return out
