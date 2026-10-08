"""Fetch the public LinkedIn job page for postings that survived the cheap
filters: full description, salary if shown, closed status, and - the part
that decides how to apply - whether it is Easy Apply or an external site.

Logged-out job pages label the Apply button with a tracking name:
'public_jobs_apply-link-onsite' means Easy Apply (apply on LinkedIn), while
'public_jobs_apply-link-offsite...' means the employer's own site (the URL
itself is only revealed after signing in).
"""

from __future__ import annotations

import random
import re
import time

from bs4 import BeautifulSoup

from autopilot.discovery.common import TIMEOUT, session
from autopilot.models import APPLY_LINKEDIN, APPLY_LINKEDIN_OFFSITE, Job
from autopilot.text import html_to_text, pick_emails

_SALARY = re.compile(r"([$€£₹]|INR|USD|EUR|GBP|SGD|AED)\s?([\d,.]+)\s?([KkMm])?(?:/(yr|year|hr|hour|mo|month))?")


def _salary(text: str):
    vals = []
    for cur, num, mult, per in _SALARY.findall(text or ""):
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        v *= {"k": 1e3, "m": 1e6}.get(mult.lower(), 1) if mult else 1
        vals.append((cur, v, per))
    if not vals:
        return None
    cur = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}.get(vals[0][0], vals[0][0])
    interval = {"hr": "hourly", "hour": "hourly", "mo": "monthly", "month": "monthly"}.get(vals[0][2], "yearly")
    return cur, min(v for _, v, _ in vals), max(v for _, v, _ in vals), interval


def enrich(job: Job, sess) -> str:
    """Fill in *job* from its public page. Returns 'ok', 'closed',
    'rate-limited' or 'error'."""
    job_id = job.url.rstrip("/").rsplit("/", 1)[-1]
    try:
        resp = sess.get(f"https://www.linkedin.com/jobs/view/{job_id}", timeout=TIMEOUT)
    except Exception:  # noqa: BLE001
        return "error"
    if resp.status_code == 429:
        return "rate-limited"
    if resp.status_code != 200 or "linkedin.com/signup" in resp.url or "authwall" in resp.url:
        return "error"
    soup = BeautifulSoup(resp.text, "html.parser")

    if re.search(r"No longer accepting applications", resp.text, re.I):
        job.flags.append("closed")
        return "closed"

    desc = soup.find("div", class_=lambda c: c and "show-more-less-html__markup" in c)
    if desc is not None:
        job.description = html_to_text(str(desc))
        if not job.hr_email:
            job.hr_email, job.alt_emails = pick_emails(job.description)

    tracking = " ".join(re.findall(r'data-tracking-control-name="(public_jobs_apply[^"]*)"', resp.text))
    if "apply-link-onsite" in tracking:
        job.apply_type = APPLY_LINKEDIN
    elif "apply-link-offsite" in tracking:
        job.apply_type = APPLY_LINKEDIN_OFFSITE
    job.apply_url = job.url

    criteria = {}
    for item in soup.select("li.description__job-criteria-item"):
        head, val = item.find("h3"), item.find("span")
        if head and val:
            criteria[head.get_text(strip=True).lower()] = val.get_text(strip=True)
    seniority = criteria.get("seniority level", "").lower()
    if seniority in ("internship", "director", "executive"):
        job.flags.append(f"seniority:{seniority}")   # screening rejects these

    caption = soup.find(class_=lambda c: c and "num-applicants__caption" in c)
    if caption is not None:
        text = caption.get_text(" ", strip=True)
        m = re.search(r"(\d[\d,]*)", text)
        if m:
            n = int(m.group(1).replace(",", ""))
            job.applicants = n + 1 if re.search(r"over|more than", text, re.I) else n

    salary_tag = soup.find("div", class_="compensation__salary")
    if salary_tag and job.salary_min is None:
        parsed = _salary(salary_tag.get_text(" ", strip=True))
        if parsed:
            job.salary_currency, job.salary_min, job.salary_max, job.salary_interval = parsed
    return "ok"


def enrich_all(jobs: list[Job], limit: int, log=print) -> None:
    """Enrich up to *limit* LinkedIn jobs, pausing between pages and stopping
    at the first sign of rate limiting."""
    # Priority order (remote first) so a tight page budget goes to the best leads.
    targets = sorted((j for j in jobs if j.source == "linkedin" and not j.description),
                     key=lambda j: j.tier or 9)[:limit]
    if not targets:
        return
    sess = session()
    counts: dict[str, int] = {}
    for i, job in enumerate(targets):
        outcome = enrich(job, sess)
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome == "rate-limited":
            log("  [linkedin] rate limited while reading job pages - continuing with what we have")
            break
        if i < len(targets) - 1:
            time.sleep(random.uniform(1.2, 2.8))
    log("  [linkedin] job pages read: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
