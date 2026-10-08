"""Google search (past week) across many job portals and recruiter posts.

Uses the SERP API keys already in job_lead_analyzer/.env (Serper, ScraperAPI,
SerpApi, Scrapingdog, SearchApi - any subset). Each result page is fetched
(public pages only, no logins) to read the full post and the recruiter's
email. The searches cover:

  recruiter posts  LinkedIn posts "share your resume at ..." (where HR emails are)
  India            Naukri, Instahyre, foundit, Hirist, iimjobs, Cutshort, Shine, TimesJobs, apna, Hirect
  global / remote  Wellfound, We Work Remotely, Remote.co, YC Work at a Startup, Built In, Dice,
                   Welcome to the Jungle, Himalayas, Greenhouse / Lever / Ashby boards
  Gulf / SE Asia   Bayt, Naukrigulf, GulfTalent, MyCareersFuture (SG), JobStreet
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from autopilot.config import Settings
from autopilot.discovery.common import TIMEOUT, detect_ats, session, to_datetime
from autopilot.models import APPLY_EMAIL, APPLY_EXTERNAL, Job
from autopilot.text import FREE_MAIL, detect_country, pick_emails

PORTALS = {
    "recruiter_posts": ["linkedin.com/posts", "linkedin.com/feed"],
    "india": ["naukri.com", "instahyre.com", "foundit.in", "hirist.tech", "iimjobs.com", "cutshort.io",
              "shine.com", "timesjobs.com", "apna.co", "hirect.in"],
    "global": ["wellfound.com", "weworkremotely.com", "remote.co", "workatastartup.com", "builtin.com",
               "dice.com", "welcometothejungle.com", "himalayas.app", "jobs.lever.co", "job-boards.greenhouse.io",
               "jobs.ashbyhq.com"],
    "gulf_asia": ["bayt.com", "naukrigulf.com", "gulftalent.com", "mycareersfuture.gov.sg", "jobstreet.com"],
}
SHARE = ('("share your resume" OR "share your cv" OR "send your resume" OR "send your cv" OR '
         '"drop your resume" OR "email your resume" OR "share resume at")')
GROUP_SHARE = {"recruiter_posts": 0.5, "india": 0.25, "global": 0.15, "gulf_asia": 0.10}
PROVIDER_ORDER = ["serper", "scraperapi", "serpapi", "scrapingdog", "searchapi"]
KEY_ENV = {"serper": "SERPER_KEY", "scraperapi": "SCRAPERAPI_KEY", "serpapi": "SERPAPI_KEY",
           "scrapingdog": "SCRAPINGDOG_KEY", "searchapi": "SEARCHAPI_KEY"}
_DETAIL = re.compile(r"job-listings-|/job/|/jobs/view/|/j/|/jobs/\d|-\d{5,}|/\d{6,}|/posts/|/feed/update/|/jobs/[a-z0-9-]+-\d+",
                     re.I)
_REL = re.compile(r"(\d+)\s*(minute|hour|day|week)s?\s*ago", re.I)


# --------------------------------------------------------------------------- #
# SERP providers (organic results, past week)
# --------------------------------------------------------------------------- #
def _serper(s, q, key):
    r = s.post("https://google.serper.dev/search", headers={"X-API-KEY": key},
               json={"q": q, "num": 10, "tbs": "qdr:w", "gl": "in", "hl": "en"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("organic", [])


def _scraperapi(s, q, key):
    r = s.get("https://api.scraperapi.com/structured/google/search",
              params={"api_key": key, "query": q, "num": 10, "country_code": "in", "tbs": "qdr:w"}, timeout=60)
    r.raise_for_status()
    return r.json().get("organic_results", [])


def _serpapi(s, q, key):
    r = s.get("https://serpapi.com/search.json",
              params={"engine": "google", "q": q, "api_key": key, "num": 10, "tbs": "qdr:w", "gl": "in"}, timeout=60)
    r.raise_for_status()
    return r.json().get("organic_results", [])


def _scrapingdog(s, q, key):
    r = s.get("https://api.scrapingdog.com/google",
              params={"api_key": key, "query": q, "results": 10, "country": "in", "tbs": "qdr:w"}, timeout=60)
    r.raise_for_status()
    return r.json().get("organic_results", [])


def _searchapi(s, q, key):
    r = s.get("https://www.searchapi.io/api/v1/search",
              params={"engine": "google", "q": q, "api_key": key, "num": 10, "time_period": "last_week"}, timeout=60)
    r.raise_for_status()
    return r.json().get("organic_results", [])


SEARCH = {"serper": _serper, "scraperapi": _scraperapi, "serpapi": _serpapi, "scrapingdog": _scrapingdog,
          "searchapi": _searchapi}


def available_providers() -> list[str]:
    return [p for p in PROVIDER_ORDER if os.getenv(KEY_ENV[p])]


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #
def plan_queries(settings: Settings, today: date | None = None) -> list[tuple[str, str, str]]:
    """[(group, location label, query)] within the per-run search budget."""
    budget = int(settings.get("sources.google_search.max_searches", 12))
    roles = settings.get("search.keywords", ["DevOps Engineer"])
    portals = settings.get("sources.google_search.portals", PORTALS) or PORTALS
    cities = settings.get("locations.india.cities", ["Pune", "Bengaluru"])
    offset = (today or date.today()).toordinal()
    places = {
        "recruiter_posts": ["India", "Remote"] + cities[:4],
        "india": ["India"] + cities[:3],
        "global": ["remote"],
        "gulf_asia": ["Dubai", "Singapore"],
    }
    alloc = {g: max(1, round(budget * GROUP_SHARE.get(g, 0.1))) for g in portals}
    while sum(alloc.values()) > budget and max(alloc.values()) > 1:
        alloc[max(alloc, key=alloc.get)] -= 1
    out = []
    for group, n in alloc.items():
        sites = "(" + " OR ".join(f"site:{d}" for d in portals[group]) + ")"
        for j in range(n):
            role = roles[(j + offset) % len(roles)]
            place = places.get(group, ["India"])[(j + offset // 3) % len(places.get(group, ["India"]))]
            if group == "recruiter_posts":
                q = f'"{role}" {SHARE} {sites} {place}'
            elif group == "global":
                q = f'"{role}" {sites} remote'
            else:
                q = f'"{role}" {sites} {place}'
            out.append((group, place, q))
    return out[:budget]


# --------------------------------------------------------------------------- #
# Results -> jobs
# --------------------------------------------------------------------------- #
def _age(text: str) -> datetime | None:
    if not text:
        return None
    m = _REL.search(text)
    now = datetime.now(timezone.utc)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        return now - timedelta(minutes=n) if unit == "minute" else now - timedelta(hours=n) if unit == "hour" \
            else now - timedelta(days=n * (7 if unit == "week" else 1))
    return to_datetime(text)


_LI_ACTIVITY = re.compile(r"(?:activity[-:]|urn:li:(?:activity|share|ugcPost):)(\d{18,20})")


def linkedin_post_date(url: str) -> datetime | None:
    """The exact time a LinkedIn post was published: its activity id's top bits are a ms timestamp."""
    m = _LI_ACTIVITY.search(url or "")
    if not m:
        return None
    try:
        dt = datetime.fromtimestamp((int(m.group(1)) >> 22) / 1000, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return dt if datetime(2015, 1, 1, tzinfo=timezone.utc) < dt <= datetime.now(timezone.utc) + timedelta(days=1) else None


def _page_text(sess, url: str) -> str:
    try:
        r = sess.get(url, timeout=12)
        if not r.ok or "authwall" in r.url or "login" in urlsplit(r.url).path:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
            tag.decompose()
        return re.sub(r"\n\s*\n+", "\n", soup.get_text("\n")).strip()[:20000]
    except Exception:  # noqa: BLE001 - a dead page just means snippet-only
        return ""


_TITLE_NOISE = re.compile(r"\s*[|\-–—]\s*(LinkedIn|Naukri(?:\.com)?|Instahyre|foundit|Indeed|Glassdoor|Wellfound|"
                          r"iimjobs|Hirist|Cutshort|Shine(?:\.com)?|Bayt(?:\.com)?|Dice|Built In).*$", re.I)


def _company_from(email: str, title: str) -> str:
    m = re.search(r"\b(?:at|@)\s+([A-Z][\w&.\- ]{1,40}?)(?:\s*[|\-–(]|$)", title)
    if m:
        return m.group(1).strip()
    domain = email.split("@")[-1] if email else ""
    if domain and domain not in FREE_MAIL:
        return domain.split(".")[0].replace("-", " ").title()
    return ""


def _to_job(group: str, place: str, role: str, item: dict, text: str) -> Job | None:
    url = item.get("link") or item.get("url") or ""
    if not url:
        return None
    title_raw = _TITLE_NOISE.sub("", str(item.get("title", ""))).strip()
    snippet = f"{item.get('snippet', '')} {' '.join(item.get('snippet_highlighted_words') or [])}"
    body = f"{text}\n{title_raw}\n{snippet}".strip()
    hr_email, alternates = pick_emails(body)
    apply_type, apply_url = detect_ats(url)
    if not hr_email and apply_type == APPLY_EXTERNAL and not _DETAIL.search(url):
        return None              # a search/listing page, not one job
    posts = group == "recruiter_posts" or "/posts/" in url or "/feed/" in url
    # Recruiter posts rarely have a clean job title in the result title: use the searched role.
    title = role if posts or len(title_raw) > 90 or not title_raw else title_raw
    domain = urlsplit(url).netloc.replace("www.", "")
    return Job(
        source=f"web:{domain}", title=title, company=_company_from(hr_email, title_raw), url=url,
        location=place if place.lower() != "remote" else "Remote", description=body[:15000],
        apply_type=APPLY_EMAIL if hr_email and apply_type == APPLY_EXTERNAL else apply_type,
        apply_url=apply_url or url, hr_email=hr_email, alt_emails=alternates,
        date_posted=linkedin_post_date(url) or _age(str(item.get("date") or item.get("date_utc") or "")),
        date_trusted=True,
        is_remote=place.lower() == "remote" or "remote" in body[:3000].lower(),
        country=detect_country(place) if place.lower() != "remote" else "",
    )


def collect(settings: Settings, log=print) -> list[Job]:
    providers = available_providers()
    if not providers:
        log("  [google] no SERP API keys (SERPER_KEY / SCRAPERAPI_KEY / ...) - skipped")
        return []
    sess = session()
    jobs, seen = [], set()
    pending = []
    for i, (group, place, query) in enumerate(plan_queries(settings)):
        provider = providers[i % len(providers)]
        role = re.match(r'"([^"]+)"', query).group(1)
        try:
            results = SEARCH[provider](sess, query, os.environ[KEY_ENV[provider]])
        except Exception as exc:  # noqa: BLE001 - next query, other provider
            log(f"  [google:{provider}] {group} search failed: {type(exc).__name__}")
            continue
        fresh = [r for r in results if (r.get("link") or r.get("url")) and (r.get("link") or r.get("url")) not in seen]
        seen.update(r.get("link") or r.get("url") for r in fresh)
        pending += [(group, place, role, r) for r in fresh]
        log(f"  [google:{provider}] {group} '{role}' {place}: {len(fresh)} results")
    with ThreadPoolExecutor(max_workers=8) as pool:
        texts = list(pool.map(lambda p: _page_text(sess, p[3].get("link") or p[3].get("url")), pending))
    for (group, place, role, item), text in zip(pending, texts):
        job = _to_job(group, place, role, item, text)
        if job:
            jobs.append(job)
    log(f"  [google] {len(jobs)} job posts ({sum(1 for j in jobs if j.hr_email)} with an HR email)")
    return jobs
