"""Free remote-job feeds: Remotive, RemoteOK, Jobicy (public JSON, no key).

Good for priority-1 remote roles. Many are restricted to one region; the
eligibility screen reads the location fields and drops what you can't take.
"""

from __future__ import annotations

from autopilot.config import Settings
from autopilot.discovery.common import TIMEOUT, detect_ats, session, to_datetime
from autopilot.models import Job
from autopilot.text import detect_country, html_to_text, pick_emails

_TAGS = ("devops", "sre", "cloud", "aws", "kubernetes", "sysadmin", "infrastructure", "platform")


def _job(source, title, company, url, location, description_html, posted, apply_link="",
         salary_min=None, salary_max=None, currency="", external_id="") -> Job:
    description = html_to_text(description_html or "")
    hr_email, alternates = pick_emails(description)
    apply_type, apply_url = detect_ats(apply_link or url)
    return Job(source=source, title=title or "", company=company or "", url=url or "",
               location=location or "Remote", description=description, apply_type=apply_type,
               apply_url=apply_url or url, hr_email=hr_email, alt_emails=alternates,
               date_posted=to_datetime(posted), is_remote=True, salary_min=salary_min,
               salary_max=salary_max, salary_currency=currency, salary_interval="yearly" if salary_min else "",
               external_id=str(external_id), country=detect_country(location or ""))


def remotive(sess) -> list[Job]:
    data = sess.get("https://remotive.com/api/remote-jobs", params={"category": "devops", "limit": 100},
                    timeout=TIMEOUT).json()
    return [_job("remotive", j.get("title"), j.get("company_name"), j.get("url"),
                 j.get("candidate_required_location"), j.get("description"), j.get("publication_date"),
                 external_id=j.get("id")) for j in data.get("jobs", [])]


def remoteok(sess) -> list[Job]:
    data = sess.get("https://remoteok.com/api", timeout=TIMEOUT).json()
    out = []
    for j in data if isinstance(data, list) else []:
        tags = [t.lower() for t in j.get("tags") or []] if isinstance(j, dict) else []
        if not isinstance(j, dict) or "position" not in j or not any(t in tags for t in _TAGS):
            continue
        out.append(_job("remoteok", j.get("position"), j.get("company"), j.get("url"), j.get("location"),
                        j.get("description"), j.get("epoch") or j.get("date"), j.get("apply_url", ""),
                        j.get("salary_min") or None, j.get("salary_max") or None, "USD", j.get("id")))
    return out


def jobicy(sess) -> list[Job]:
    data = sess.get("https://jobicy.com/api/v2/remote-jobs", params={"count": 50, "tag": "devops"},
                    timeout=TIMEOUT).json()
    return [_job("jobicy", j.get("jobTitle"), j.get("companyName"), j.get("url"), j.get("jobGeo"),
                 j.get("jobDescription") or j.get("jobExcerpt"), j.get("pubDate"),
                 salary_min=j.get("annualSalaryMin"), salary_max=j.get("annualSalaryMax"),
                 currency=j.get("salaryCurrency") or "", external_id=j.get("id"))
            for j in data.get("jobs", [])]


def collect(settings: Settings, log=print) -> list[Job]:
    sess = session()
    jobs = []
    for name, fn in (("remotive", remotive), ("remoteok", remoteok), ("jobicy", jobicy)):
        try:
            jobs.extend(fn(sess))
        except Exception as exc:  # noqa: BLE001 - feeds are best-effort
            log(f"  [{name}] skipped: {type(exc).__name__}: {str(exc)[:100]}")
    return jobs
