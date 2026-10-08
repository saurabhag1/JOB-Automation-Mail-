"""Shared bits for the job sources: HTTP session, ATS URL detection, dates."""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlsplit

import requests
from dateutil import parser as date_parser
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from autopilot.models import APPLY_ASHBY, APPLY_EXTERNAL, APPLY_GREENHOUSE, APPLY_LEVER

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
TIMEOUT = 20


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    retry = Retry(total=2, backoff_factor=1.5, status_forcelist=(500, 502, 503, 504),
                  allowed_methods=("GET",))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


_GREENHOUSE = re.compile(r"(?:job-)?boards\.greenhouse\.io/([\w.-]+)/jobs/(\d+)", re.I)
_LEVER = re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w.-]+)/([0-9a-f]{8}-[0-9a-f-]{27,})", re.I)
_ASHBY = re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f]{8}-[0-9a-f-]{27,})", re.I)


def detect_ats(url: str) -> tuple[str, str]:
    """(apply_type, canonical form URL) for Greenhouse / Lever / Ashby links.

    Anything else (Workday, iCIMS, company sites...) is 'external': those need
    an account per company, so they are handed to you as a clean link instead.
    """
    if not url:
        return APPLY_EXTERNAL, ""
    if m := _GREENHOUSE.search(url):
        return APPLY_GREENHOUSE, f"https://job-boards.greenhouse.io/{m.group(1)}/jobs/{m.group(2)}"
    if "greenhouse.io/embed/job_app" in url:
        qs = parse_qs(urlsplit(url).query)
        if qs.get("for") and qs.get("token"):
            return APPLY_GREENHOUSE, (f"https://job-boards.greenhouse.io/{qs['for'][0]}/jobs/"
                                      f"{qs['token'][0]}")
    if m := _LEVER.search(url):
        host = "jobs.eu.lever.co" if "eu.lever.co" in url.lower() else "jobs.lever.co"
        return APPLY_LEVER, f"https://{host}/{m.group(1)}/{m.group(2)}/apply"
    if m := _ASHBY.search(url):
        return APPLY_ASHBY, f"https://jobs.ashbyhq.com/{m.group(1)}/{m.group(2)}/application"
    return APPLY_EXTERNAL, url


def clean(value):
    """pandas NaN / 'nan' / None -> None."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str) and value.strip().lower() in ("", "nan", "none", "n/a"):
        return None
    return value


def to_datetime(value) -> datetime | None:
    """Epoch seconds/ms, date, datetime or a date string -> aware UTC datetime."""
    value = clean(value)
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, date):
            dt = datetime(value.year, value.month, value.day)
        elif isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            num = float(value)
            dt = datetime.fromtimestamp(num / 1000 if num > 1e11 else num, timezone.utc)
        else:
            dt = date_parser.parse(str(value))
    except (ValueError, OverflowError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


_MAIL_OK: dict[str, bool] = {}


def mail_domain_ok(domain: str) -> bool:
    """True if *domain* can receive email (has MX, or at least an A record).
    Unknown (DNS lookup failed) counts as OK - only proven dead domains are dropped."""
    domain = (domain or "").lower().strip()
    if not domain:
        return False
    if domain not in _MAIL_OK:
        ok = True
        try:
            for rtype in ("MX", "A"):
                r = requests.get("https://dns.google/resolve", params={"name": domain, "type": rtype}, timeout=8)
                data = r.json()
                if data.get("Status") == 3:          # NXDOMAIN: the domain does not exist
                    ok = False
                    break
                if data.get("Answer"):
                    ok = True
                    break
                ok = False
        except Exception:  # noqa: BLE001
            ok = True
        _MAIL_OK[domain] = ok
    return _MAIL_OK[domain]
