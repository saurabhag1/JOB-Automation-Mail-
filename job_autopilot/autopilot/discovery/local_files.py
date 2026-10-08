"""The repo's existing lead files, as opt-in "takeover" sources.

* job_lead_analyzer/daily_job_leads.json - HR emails found in public hiring posts
* bulk_mail_sender/Pasted text(1).txt     - the daily job list (with full JDs)

Today GitHub Actions mails both lists a generic template. Enabling a source
here lets the autopilot send a tailored resume instead - but only switch it on
after removing the matching generic send, or the recruiter is mailed twice.
"""

from __future__ import annotations

import json

from autopilot.config import Settings
from autopilot.discovery.common import to_datetime
from autopilot.models import APPLY_EMAIL, Job
from autopilot.text import classify_email, detect_country, html_to_text


def _load(settings: Settings, key: str):
    path = settings.path(f"sources.{key}.path", "")
    if not path.exists():
        return None, path
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh), path


def hr_email_leads(settings: Settings, log=print) -> list[Job]:
    data, path = _load(settings, "hr_email_leads")
    if data is None:
        log(f"  [hr-leads] {path} not found")
        return []
    jobs = []
    for lead in data.get("leads", []):
        email = (lead.get("email") or "").lower()
        if classify_email(email) == "reject":
            continue
        location = lead.get("location", "")
        skills = ", ".join(lead.get("skills", []))
        jobs.append(Job(
            source="hr-leads", title=lead.get("position") or "DevOps Engineer",
            company=lead.get("company", ""), url=lead.get("url", ""), location=location,
            # The collector keeps only a short record; its skills list stands in for the JD.
            description=lead.get("description") or f"{lead.get('position', '')}. Skills: {skills}. "
                                                   f"Experience: {lead.get('experience', '')}",
            apply_type=APPLY_EMAIL, hr_email=email, alt_emails=lead.get("alternateEmails", []),
            date_posted=to_datetime(lead.get("datePosted")), date_trusted=True,
            is_remote="remote" in location.lower(), experience_text=lead.get("experience", ""),
            external_id=email, country=detect_country(location) or "India",
        ))
    return jobs


def pasted_jobs(settings: Settings, log=print) -> list[Job]:
    data, path = _load(settings, "pasted_jobs")
    if data is None:
        log(f"  [pasted] {path} not found")
        return []
    jobs = []
    for item in data.get("jobs", []):
        emails = [e.strip().lower() for e in str(item.get("emails", "")).split(";") if e.strip()]
        emails = [e for e in emails if classify_email(e) != "reject"]
        if not emails:
            continue
        location = item.get("locations", "")
        lo, hi = item.get("experienceMin"), item.get("experienceMax")
        jobs.append(Job(
            source="pasted", title=item.get("jobType") or "DevOps Engineer", company="",
            url="", location=location, description=html_to_text(item.get("jdHtml", "")),
            apply_type=APPLY_EMAIL, hr_email=emails[0], alt_emails=emails[1:],
            date_posted=to_datetime(item.get("createdAt")), date_trusted=True,
            is_remote="remote" in location.lower(),
            experience_text=f"{lo}-{hi} years" if lo is not None and hi is not None else "",
            external_id=str(item.get("jobId") or emails[0]), country=detect_country(location) or "India",
        ))
    return jobs
