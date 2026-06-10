"""Supplement / no-key fallback: free public job-board JSON APIs.

These feeds (RemoteOK, Remotive, Arbeitnow, Jobicy) require no API key and are
great for company / role / location coverage of remote jobs. They rarely
publish a personal HR email, so on their own they yield few "genuine HR email"
leads -- but they keep the tool useful when no SERPAPI_KEY is configured.
"""

import requests

import config

_HEADERS = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}


def _get_json(url, params=None):
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"  [feeds] {url} failed: {exc}")
        return None


def _post(url, text, source, company="", location="", date_posted="", title=""):
    return {
        "url": url,
        "text": text,
        "source": source,
        "company": company,
        "location": location,
        "date_posted": str(date_posted or ""),
        "title": str(title or ""),
    }


def _remotive(query):
    data = _get_json("https://remotive.com/api/remote-jobs", {"search": query, "limit": 40})
    out = []
    for job in (data or {}).get("jobs", []):
        text = "\n".join(
            str(job.get(k, "")) for k in ("title", "company_name", "candidate_required_location", "description")
        )
        out.append(_post(job.get("url", ""), text, "remotive",
                         job.get("company_name", ""), job.get("candidate_required_location", ""),
                         job.get("publication_date", ""), job.get("title", "")))
    return out


def _remoteok():
    data = _get_json("https://remoteok.com/api")
    out = []
    if isinstance(data, list):
        for job in data:
            if not isinstance(job, dict) or "position" not in job:
                continue
            text = "\n".join(
                str(job.get(k, "")) for k in ("position", "company", "location", "description")
            )
            text += " " + " ".join(job.get("tags", []) or [])
            out.append(_post(job.get("url", ""), text, "remoteok",
                             job.get("company", ""), job.get("location", ""),
                             job.get("date", ""), job.get("position", "")))
    return out


def _arbeitnow():
    data = _get_json("https://www.arbeitnow.com/api/job-board-api")
    out = []
    for job in (data or {}).get("data", []):
        text = "\n".join(
            str(job.get(k, "")) for k in ("title", "company_name", "location", "description")
        )
        text += " " + " ".join(job.get("tags", []) or [])
        out.append(_post(job.get("url", ""), text, "arbeitnow",
                         job.get("company_name", ""), job.get("location", ""),
                         job.get("created_at", ""), job.get("title", "")))
    return out


def _jobicy(query):
    data = _get_json("https://jobicy.com/api/v2/remote-jobs", {"count": 50, "tag": query})
    out = []
    for job in (data or {}).get("jobs", []):
        text = "\n".join(
            str(job.get(k, "")) for k in ("jobTitle", "companyName", "jobGeo", "jobExcerpt", "jobDescription")
        )
        out.append(_post(job.get("url", ""), text, "jobicy",
                         job.get("companyName", ""), job.get("jobGeo", ""),
                         job.get("pubDate", ""), job.get("jobTitle", "")))
    return out


def collect(query="devops"):
    """Return raw posts from all free feeds. Failures are skipped, not fatal."""
    posts = []
    for fn in (lambda: _remotive(query), _remoteok, _arbeitnow, lambda: _jobicy(query)):
        try:
            posts.extend(fn())
        except Exception as exc:  # noqa: BLE001 - feeds are best-effort
            print(f"  [feeds] source error: {exc}")
    print(f"  [feeds] collected {len(posts)} candidate posts.")
    return posts
