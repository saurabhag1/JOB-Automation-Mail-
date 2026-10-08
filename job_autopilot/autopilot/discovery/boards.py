"""LinkedIn, Indeed, Naukri and Glassdoor via python-jobspy.

JobSpy reads the boards' public, logged-out search endpoints, so discovery
never touches your accounts. Every query is age-filtered on the server
(hours_old), which is what lets the freshness check trust these dates.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import date

from autopilot.config import Settings
from autopilot.discovery.common import clean, detect_ats, to_datetime
from autopilot.models import APPLY_EXTERNAL, APPLY_NAUKRI, Job
from autopilot.text import detect_country, pick_emails

SITES = ("linkedin", "indeed", "naukri", "glassdoor", "bayt")

# Indeed / Glassdoor run one domain per country.
INDEED_COUNTRY = {
    "India": "india", "Singapore": "singapore", "UAE": "united arab emirates", "Japan": "japan",
    "Netherlands": "netherlands", "Germany": "germany", "United Kingdom": "uk",
    "United States": "usa", "Canada": "canada", "Australia": "australia", "Ireland": "ireland",
}
TIER_SHARE = {1: 0.4, 2: 0.35, 3: 0.25}   # how a site's query budget is split across priorities


@dataclass(frozen=True)
class Query:
    site: str
    keyword: str
    location: str | None
    is_remote: bool
    country: str      # our country label for the results ("India", "Worldwide", ...)
    tier: int


def _specs(site: str, settings: Settings) -> dict[int, list[dict]]:
    """Location specs per priority tier, shaped for what each site supports."""
    tiers: dict[int, list[dict]] = {1: [], 2: [], 3: []}
    cities = settings.get("locations.india.cities", [])
    if settings.get("locations.remote.enabled", True):
        # Remote roles open to India. (A LinkedIn "Worldwide" search mostly returns remote
        # roles tied to one foreign country, so it is not used; ATS boards and remote feeds
        # cover genuinely global remote work.)
        tiers[1].append(dict(location="India", is_remote=True, country="India"))
    if settings.get("locations.india.enabled", True):
        if site == "naukri":
            tiers[2].append(dict(location=None, is_remote=False, country="India"))
            tiers[2] += [dict(location=c.lower(), is_remote=False, country="India") for c in cities]
        else:
            tiers[2].append(dict(location="India", is_remote=False, country="India"))
            tiers[2] += [dict(location=f"{c}, India", is_remote=False, country="India") for c in cities]
    if settings.get("locations.international.enabled", True) and site != "naukri":
        for place in settings.get("locations.international.places", []):
            country = detect_country(place)
            if site in ("indeed", "glassdoor") and country not in INDEED_COUNTRY:
                continue
            tiers[3].append(dict(location=place, is_remote=False, country=country))
    if site == "naukri":
        tiers[1] = [dict(location=None, is_remote=True, country="India")] if tiers[1] else []
    if site == "bayt":   # Gulf jobs board: abroad tier only
        return {1: [], 2: [], 3: [dict(location="United Arab Emirates", is_remote=False, country="UAE")]}
    return tiers


def plan_queries(site: str, settings: Settings, today: date | None = None) -> list[Query]:
    """Spread a small per-site budget across priorities, locations and keywords.

    Remote gets ~40%, India ~35%, abroad ~25%. Keywords and locations rotate
    with the date, so consecutive daily runs cover different combinations
    instead of repeating the same few searches.
    """
    keywords = settings.get("search.keywords", ["DevOps Engineer"])
    budget = int(settings.get(f"sources.{site}.max_queries", 6))
    tiers = {t: specs for t, specs in _specs(site, settings).items() if specs}
    if not tiers or budget <= 0:
        return []
    alloc = {t: max(1, round(budget * TIER_SHARE[t])) for t in tiers}
    while sum(alloc.values()) > budget and max(alloc.values()) > 1:
        alloc[max(alloc, key=lambda t: (alloc[t], t))] -= 1
    offset = (today or date.today()).toordinal()
    queries: list[Query] = []
    for tier, n in sorted(alloc.items()):
        specs = tiers[tier]
        for j in range(n):
            spec = specs[(j + offset) % len(specs)]
            keyword = keywords[(j + offset + tier) % len(keywords)]
            q = Query(site, keyword, spec["location"], spec["is_remote"], spec["country"], tier)
            if q not in queries:
                queries.append(q)
    return queries[:budget]


def _run_query(q: Query, settings: Settings):
    from jobspy import scrape_jobs  # heavy import (pandas); only when actually scraping

    hours = int(settings.get("search.max_age_days", 7)) * 24
    kwargs = dict(
        site_name=[q.site], search_term=q.keyword, results_wanted=int(settings.get(f"sources.{q.site}.per_query", 15)),
        hours_old=hours, is_remote=q.is_remote, description_format="markdown", verbose=0,
    )
    if q.location:
        kwargs["location"] = q.location
    if q.site in ("indeed", "glassdoor"):
        kwargs["country_indeed"] = INDEED_COUNTRY.get(q.country, "india")
    if q.site == "naukri":
        kwargs["fetch_description"] = True   # Naukri descriptions often carry the HR email
    return scrape_jobs(**kwargs)


def _to_job(row: dict, q: Query) -> Job:
    description = clean(row.get("description")) or ""
    location = clean(row.get("location")) or ""
    url = clean(row.get("job_url")) or ""
    direct = clean(row.get("job_url_direct")) or ""
    hr_email, alternates = pick_emails(description)

    apply_type, apply_url = detect_ats(direct) if direct else (APPLY_EXTERNAL, url)
    if q.site == "naukri" and not direct:
        apply_type, apply_url = APPLY_NAUKRI, url          # applied to on naukri.com itself
    elif q.site == "linkedin":
        apply_type, apply_url = APPLY_EXTERNAL, url        # decided later by linkedin_details

    country = detect_country(location)
    if (q.site == "naukri" or (q.site in ("indeed", "glassdoor") and location)) and q.country not in ("Worldwide", ""):
        country = q.country                                 # domain-scoped search: trust it
    wfh = (clean(row.get("work_from_home_type")) or "").lower()
    is_remote = bool(clean(row.get("is_remote"))) or q.is_remote or "remote" in wfh \
        or "remote" in location.lower()

    return Job(
        source=q.site,
        title=clean(row.get("title")) or "",
        company=clean(row.get("company")) or "",
        url=url,
        location=location,
        description=description,
        apply_url=apply_url,
        apply_type=apply_type,
        hr_email=hr_email,
        alt_emails=alternates,
        date_posted=to_datetime(row.get("date_posted")),
        date_trusted=True,
        is_remote=is_remote,
        salary_min=clean(row.get("min_amount")),
        salary_max=clean(row.get("max_amount")),
        salary_currency=clean(row.get("currency")) or "",
        salary_interval=clean(row.get("interval")) or "",
        experience_text=clean(row.get("experience_range")) or "",
        external_id=str(clean(row.get("id")) or ""),
        tier=q.tier,
        country=country or (q.country if q.country not in ("Worldwide", "") else ""),
    )


def collect(site: str, settings: Settings, log=print) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    queries = plan_queries(site, settings)
    for i, q in enumerate(queries):
        where = ("remote " if q.is_remote else "") + (q.location or "anywhere")
        try:
            df = _run_query(q, settings)
        except Exception as exc:  # noqa: BLE001 - one bad query must not end the site
            log(f"  [{site}] '{q.keyword}' @ {where}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        added = 0
        for row in df.to_dict("records") if len(df) else []:
            job = _to_job(row, q)
            if job.url and job.id not in seen:
                seen.add(job.id)
                jobs.append(job)
                added += 1
        log(f"  [{site}] '{q.keyword}' @ {where}: {added} new")
        if i < len(queries) - 1:
            time.sleep(random.uniform(2.0, 5.0))   # stay polite; boards throttle bursts
    return jobs
