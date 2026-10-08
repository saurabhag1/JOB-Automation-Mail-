"""Greenhouse, Lever and Ashby public job-board APIs.

These are the employers' own postings (no reposts, exact timestamps) and the
forms behind them are standard, so the bot can fill them. Board names come
from settings.yaml; every default there was checked against the live API.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from autopilot.config import Settings
from autopilot.discovery.common import TIMEOUT, session, to_datetime
from autopilot.models import APPLY_ASHBY, APPLY_GREENHOUSE, APPLY_LEVER, Job
from autopilot.text import detect_country, html_to_text, pick_emails

_INTERVAL = {"1 YEAR": "yearly", "1 MONTH": "monthly", "1 WEEK": "weekly", "1 DAY": "daily",
             "1 HOUR": "hourly", "per-year-salary": "yearly", "per-month-salary": "monthly",
             "per-hour-wage": "hourly"}


def _fresh(dt: datetime | None, days: int) -> bool:
    return dt is None or dt >= datetime.now(timezone.utc) - timedelta(days=days)


def greenhouse(token: str, keep, days: int, sess) -> list[Job]:
    base = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    listing = sess.get(base, timeout=TIMEOUT)
    listing.raise_for_status()
    jobs = []
    for item in listing.json().get("jobs", []):
        posted = to_datetime(item.get("first_published") or item.get("updated_at"))
        if not keep(item.get("title", "")) or not _fresh(posted, days):
            continue
        detail = sess.get(f"{base}/{item['id']}", timeout=TIMEOUT)
        if not detail.ok:
            continue
        data = detail.json()
        description = html_to_text(data.get("content", ""))
        location = (data.get("location") or {}).get("name", "")
        hr_email, alternates = pick_emails(description)
        jobs.append(Job(
            source="greenhouse", title=data.get("title", ""),
            company=data.get("company_name") or token.replace("-", " ").title(),
            url=data.get("absolute_url") or f"https://job-boards.greenhouse.io/{token}/jobs/{item['id']}",
            location=location, description=description, apply_type=APPLY_GREENHOUSE,
            # The embedded form loads even for companies whose board redirects to their own site.
            apply_url=f"https://job-boards.greenhouse.io/embed/job_app?for={token}&token={item['id']}",
            hr_email=hr_email, alt_emails=alternates, date_posted=posted,
            is_remote="remote" in location.lower(), external_id=f"{token}:{item['id']}",
            country=detect_country(location),
        ))
    return jobs


def lever(site: str, keep, days: int, sess) -> list[Job]:
    resp = sess.get(f"https://api.lever.co/v0/postings/{site}?mode=json", timeout=TIMEOUT)
    resp.raise_for_status()
    jobs = []
    for p in resp.json() if isinstance(resp.json(), list) else []:
        posted = to_datetime(p.get("createdAt"))
        if not keep(p.get("text", "")) or not _fresh(posted, days):
            continue
        cats = p.get("categories") or {}
        location = cats.get("location") or ", ".join(cats.get("allLocations") or [])
        parts = [p.get("descriptionPlain", "")]
        for block in p.get("lists") or []:
            parts.append(f"{block.get('text', '')}\n{html_to_text(block.get('content', ''))}")
        parts.append(p.get("additionalPlain", ""))
        description = "\n\n".join(x for x in parts if x).strip()
        salary = p.get("salaryRange") or {}
        hr_email, alternates = pick_emails(description)
        jobs.append(Job(
            source="lever", title=p.get("text", ""), company=site.replace("-", " ").title(),
            url=p.get("hostedUrl", ""), location=location, description=description,
            apply_type=APPLY_LEVER, apply_url=p.get("applyUrl") or f"{p.get('hostedUrl', '')}/apply",
            hr_email=hr_email, alt_emails=alternates, date_posted=posted,
            is_remote=(p.get("workplaceType") == "remote") or "remote" in location.lower(),
            salary_min=salary.get("min"), salary_max=salary.get("max"),
            salary_currency=salary.get("currency") or "", salary_interval=_INTERVAL.get(salary.get("interval"), ""),
            external_id=f"{site}:{p.get('id')}", country=detect_country(location),
        ))
    return jobs


def ashby(org: str, keep, days: int, sess) -> list[Job]:
    resp = sess.get(f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true",
                    timeout=TIMEOUT)
    resp.raise_for_status()
    jobs = []
    for p in resp.json().get("jobs", []):
        # publishedAt is the *last* publish time, so re-published old roles look new;
        # the ghost-job check (seen for weeks) catches those over time.
        posted = to_datetime(p.get("publishedAt"))
        if not p.get("isListed", True) or not keep(p.get("title", "")) or not _fresh(posted, days):
            continue
        locations = [p.get("location") or ""] + [s.get("location", "") for s in p.get("secondaryLocations") or []]
        location = ", ".join(x for x in locations if x)
        comp = next((c for c in (p.get("compensation") or {}).get("summaryComponents") or []
                     if c.get("compensationType") == "Salary"), {})
        description = p.get("descriptionPlain") or html_to_text(p.get("descriptionHtml", ""))
        hr_email, alternates = pick_emails(description)
        jobs.append(Job(
            source="ashby", title=p.get("title", ""), company=org.replace("-", " ").title(),
            url=p.get("jobUrl", ""), location=location, description=description,
            apply_type=APPLY_ASHBY, apply_url=p.get("applyUrl") or f"{p.get('jobUrl', '')}/application",
            hr_email=hr_email, alt_emails=alternates, date_posted=posted,
            is_remote=bool(p.get("isRemote")) or p.get("workplaceType") == "Remote",
            salary_min=comp.get("minValue"), salary_max=comp.get("maxValue"),
            salary_currency=comp.get("currencyCode") or "", salary_interval=_INTERVAL.get(comp.get("interval"), ""),
            external_id=f"{org}:{p.get('id')}", country=detect_country(location),
        ))
    return jobs


def collect(settings: Settings, log=print) -> list[Job]:
    from autopilot.screening import title_reason

    days = int(settings.get("search.max_age_days", 7))
    keep = lambda title: not title_reason(title, settings)  # noqa: E731
    boards = ([("greenhouse", t) for t in settings.get("sources.ats_boards.greenhouse", [])]
              + [("lever", t) for t in settings.get("sources.ats_boards.lever", [])]
              + [("ashby", t) for t in settings.get("sources.ats_boards.ashby", [])])
    fetchers = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}
    sess = session()

    def run(kind_token):
        kind, token = kind_token
        try:
            return fetchers[kind](token, keep, days, sess)
        except Exception as exc:  # noqa: BLE001 - a renamed/closed board is skipped
            log(f"  [{kind}:{token}] skipped: {type(exc).__name__}: {str(exc)[:100]}")
            return []

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, boards))
    return [job for batch in results for job in batch]
