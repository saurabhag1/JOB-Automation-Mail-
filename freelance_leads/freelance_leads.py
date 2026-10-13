#!/usr/bin/env python3
"""Freelance / contract DevOps lead agent.

    python3 freelance_leads/freelance_leads.py            find leads, draft emails, write the Excel (sends nothing)
    python3 freelance_leads/freelance_leads.py --send     ...and email every new HR contact found
    python3 freelance_leads/freelance_leads.py --send --max-send 10

Each run:
  1. searches for contract / freelance / part-time / project DevOps work (remote or India):
     Google (SERP keys, past week) on LinkedIn recruiter posts and contract-friendly portals,
     OpenRouter web search, Indeed + LinkedIn with their contract/part-time filters, Naukri,
     and the Remotive / Jobicy / Himalayas feeds (DuckDuckGo when no SERP key is set);
  2. keeps genuine, fresh posts that really are contract-type, open to India/remote, and match
     your resume (job_autopilot's screening: scam words, region, experience, skill match);
  3. drafts a short HTML email per lead with an HR address (your 3 most relevant achievements,
     matching skills, contact block) and, with --send, sends it from your Gmail with your resume;
  4. writes freelance_leads/runs/devops_leads_YYYYMMDD_HHMMSS.xlsx (SR, Company Name, Position,
     Works / Description, Location, Salary / Rate, Contract Type, Contact / HR Email, Job Role,
     Status) plus the drafts, and emails you the workbook.

Keys come only from variables that already exist: EMAIL_ADDRESS, EMAIL_PASSWORD (SMTP_HOST/
SMTP_PORT optional), SERPER_KEY / SERPAPI_KEY / SCRAPERAPI_KEY / SCRAPINGDOG_KEY / SEARCHAPI_KEY,
GEMINI_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY, REPORT_EMAIL (optional). Your details and
resume come from job_autopilot/profile.yaml and bulk_mail_sender/Saurabh_Agrawal_2026.pdf.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
APP = REPO / "job_autopilot"
VENV = APP / ".venv"

if os.path.realpath(sys.prefix) != os.path.realpath(VENV) and (VENV / "bin" / "python").exists():
    os.execv(str(VENV / "bin" / "python"), [str(VENV / "bin" / "python"), str(Path(__file__).resolve()), *sys.argv[1:]])
sys.path.insert(0, str(APP))

import argparse  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import random  # noqa: E402
import re  # noqa: E402
import smtplib  # noqa: E402
import ssl  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from collections import Counter  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from email.message import EmailMessage  # noqa: E402
from email.utils import formatdate  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402

import requests  # noqa: E402
import yaml  # noqa: E402
from requests.adapters import HTTPAdapter  # noqa: E402
from urllib3.util.retry import Retry  # noqa: E402

from autopilot.config import load_env, load_profile, load_settings  # noqa: E402
from autopilot.discovery import ai_search, boards, google_search as gs  # noqa: E402
from autopilot.discovery.common import USER_AGENT, mail_domain_ok, to_datetime  # noqa: E402
from autopilot.models import Job, canonical_url  # noqa: E402
from autopilot.text import EMAIL_RE, display, find_terms, html_to_text, implied  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30), "IST")
COLUMNS = ["SR", "Company Name", "Position", "Works / Description", "Location", "Salary / Rate",
           "Contract Type", "Contact / HR Email", "Job Role", "Status"]
SENT, DRAFTED, REVIEW = "Sent", "Email Drafted", "Pending Review"
log = logging.getLogger("freelance")


# --------------------------------------------------------------------------- #
# Logging: in GitHub Actions (public logs) email addresses are masked
# --------------------------------------------------------------------------- #
class _MaskFormatter(logging.Formatter):
    def format(self, record):
        return EMAIL_RE.sub("<email>", super().format(record))


def setup_logging(run_dir: Path) -> None:
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(_MaskFormatter(fmt._fmt, fmt.datefmt) if os.getenv("CI") else fmt)
    file = logging.FileHandler(run_dir / "run.log", encoding="utf-8")   # full log: goes to your inbox only
    file.setFormatter(fmt)
    log.handlers[:] = [console, file]


# --------------------------------------------------------------------------- #
# Polite HTTP: retries + at most one request per host every 1.5 s
# --------------------------------------------------------------------------- #
class ThrottledSession(requests.Session):
    def __init__(self, gap: float = 1.5):
        super().__init__()
        self.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
        retry = Retry(total=2, backoff_factor=1.5, status_forcelist=(500, 502, 503, 504), allowed_methods=("GET",))
        self.mount("https://", HTTPAdapter(max_retries=retry))
        self.gap, self._last, self._lock = gap, {}, threading.Lock()

    def request(self, method, url, *args, **kwargs):
        host = urlsplit(url).netloc
        with self._lock:
            wait = self._last.get(host, 0) + self.gap - time.time()
            self._last[host] = max(time.time(), self._last.get(host, 0) + self.gap)
        if wait > 0:
            time.sleep(wait)
        kwargs.setdefault("timeout", 25)
        return super().request(method, url, *args, **kwargs)


# --------------------------------------------------------------------------- #
# Contract type, rate, role
# --------------------------------------------------------------------------- #
_TYPE_RULES = [
    ("Freelance", re.compile(r"\bfreelanc\w*|\bgig\b|independent contractor", re.I)),
    ("Contract", re.compile(r"\bc2h\b|contract[\s-]*to[\s-]*hire|\bc2c\b|corp[\s-]*to[\s-]*corp|\bcontractual\b|"
                            r"(?<!employment )(?<!smart )\bcontract\b(?!\s*(?:law|management|negotiat|lifecycle|"
                            r"review|drafting|manufactur))|third[\s-]*party payroll|\btemp(?:orary)? role\b", re.I)),
    ("Part-Time", re.compile(r"part[\s-]*time|\b\d{1,2}\s*(?:-\s*\d{1,2}\s*)?(?:hrs?|hours)\s*(?:/|per|a)\s*week", re.I)),
    ("Project", re.compile(r"project[\s-]*based|one[\s-]*time project|short[\s-]*term (?:project|engagement)|"
                           r"fixed[\s-]*price|per project", re.I)),
]
_FEED_TYPE = {"contract": "Contract", "freelance": "Freelance", "part_time": "Part-Time", "part-time": "Part-Time",
              "parttime": "Part-Time", "contractor": "Contract", "temporary": "Contract"}


def contract_type(job: Job, declared: str = "") -> str:
    """Freelance / Contract / Part-Time / Project, or '' for a regular full-time post."""
    if declared:
        return _FEED_TYPE.get(declared.lower().replace(" ", "_"), _FEED_TYPE.get(declared.lower(), ""))
    for text in (job.title, job.description[:2500]):
        for label, rx in _TYPE_RULES:
            if rx.search(text or ""):
                return label
    return ""


_MONEY = r"(?:₹|rs\.?|inr|\$|usd|€|eur|£|gbp|aed|sgd)\s?\d[\d,]*(?:\.\d+)?\s*(?:k|l|lakhs?|lpa)?"
_RATE = re.compile(rf"{_MONEY}(?:\s*(?:-|–|to)\s*(?:{_MONEY}|\d[\d,]*(?:\.\d+)?\s*(?:k|l|lakhs?)?))?"
                   rf"\s*(?:/|per|an?)\s*(?:hr|hour|h|day|week|month|mo|annum|year|yr)\b|"
                   rf"\d+(?:\.\d+)?\s*(?:-|–|to)\s*\d+(?:\.\d+)?\s*(?:lpa|lakhs? per annum)|"
                   rf"{_MONEY}\s*(?:-|–|to)\s*{_MONEY}", re.I)


def rate_of(job: Job) -> str:
    if job.salary_min or job.salary_max:
        lo, hi = job.salary_min, job.salary_max
        amount = f"{lo:,.0f}-{hi:,.0f}" if lo and hi and lo != hi else f"{(lo or hi):,.0f}"
        return f"{job.salary_currency or ''} {amount} / {job.salary_interval or 'period n/a'}".strip()
    m = _RATE.search(f"{job.title}\n{job.description[:6000]}")
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else "Not stated"


_FAMILY = [("SRE / Reliability", r"\bsre\b|reliability"), ("MLOps", r"mlops"), ("DevSecOps", r"devsecops|security"),
           ("Platform / Kubernetes", r"platform|kubernetes|k8s"), ("Cloud Infrastructure", r"cloud|aws|azure|gcp"),
           ("Infrastructure as Code", r"terraform|iac")]


def job_role(job: Job) -> str:
    family = next((name for name, rx in _FAMILY if re.search(rx, job.title, re.I)), "DevOps / CI-CD")
    skills = display(job.matched[:3])
    return f"{family} - {', '.join(skills)}" if skills else family


def location_label(job: Job) -> str:
    if job.is_remote:
        return "Remote (Global)" if re.search(r"worldwide|anywhere|global", job.location or "", re.I) else \
            f"Remote ({job.location.strip()})" if job.location and job.location.lower() != "remote" else "Remote"
    return f"India - {job.location}" if job.location and "india" not in job.location.lower() else (job.location or "India")


def brief(text: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", html_to_text(text or "")).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for s in sentences:
        if len(out) + len(s) > limit:
            break
        out += (" " if out else "") + s
    return out or text[:limit]


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
CONTRACT_WORDS = '("contract" OR "freelance" OR "part-time" OR "C2H" OR "contract to hire" OR "project based")'
PORTALS = "(site:cutshort.io OR site:wellfound.com OR site:arc.dev OR site:contra.com OR site:remote.co OR " \
          "site:himalayas.app OR site:naukri.com OR site:foundit.in OR site:instahyre.com)"


@dataclass
class Lead:
    job: Job
    kind: str
    declared: str = ""
    summary: str = ""
    status: str = REVIEW
    note: str = ""
    draft: Path | None = None


def google_queries(cfg: dict, day: int) -> list[tuple[str, str, str]]:
    kws, places = cfg["keywords"], cfg["places"]
    n = int(cfg["sources"]["google"].get("max_searches", 14))
    out = []
    for i in range(n):
        kw, place = kws[(i + day) % len(kws)], places[(i + day // 2) % len(places)]
        if i % 5 < 3:     # 60% recruiter posts (where HR emails are), 40% contract-friendly portals
            out.append(("recruiter_posts", place, f'"{kw}" {CONTRACT_WORDS} {gs.SHARE} '
                                                  f'(site:linkedin.com/posts OR site:linkedin.com/feed) {place}'))
        else:
            out.append(("india", place, f'"{kw}" {CONTRACT_WORDS} {PORTALS} {place}'))
    return out


def from_search(sess, cfg: dict, settings) -> list[Lead]:
    day = datetime.now(IST).toordinal()
    providers = gs.available_providers()
    tasks: list[tuple[str, str, str, str]] = []          # (engine, group, place, query)
    if cfg["sources"]["google"].get("enabled", True) and providers:
        tasks += [("google", g, p, q) for g, p, q in google_queries(cfg, day)]
    elif cfg["sources"]["duckduckgo"].get("enabled", True):
        tasks += [("ddg", g, p, q.replace(" OR site:", " OR site:")) for g, p, q in
                  google_queries(cfg, day)[: int(cfg["sources"]["duckduckgo"].get("max_searches", 8))]]
    if cfg["sources"]["web"].get("enabled", True) and os.getenv("OPENROUTER_API_KEY"):
        for i in range(int(cfg["sources"]["web"].get("max_searches", 6))):
            kw, place = cfg["keywords"][(i + day) % len(cfg["keywords"])], cfg["places"][i % len(cfg["places"])]
            tasks.append(("web", "recruiter_posts", place,
                          f"hiring {kw} contract freelance part-time {place} send resume email"))
    leads, seen = [], set()
    for i, (engine, group, place, query) in enumerate(tasks):
        role = (re.match(r'"([^"]+)"', query) or re.search(r"hiring (.+?) contract", query)).group(1)
        try:
            if engine == "google":
                items = gs.search_any(sess, query, providers, i, log.info)   # dead keys skipped for the run
            elif engine == "web":
                items = ai_search.web_search(query, settings, sess)
            else:
                items = duckduckgo(sess, query)
        except ai_search.WebSearchUnavailable as exc:
            log.info(f"  [web] {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - never print the exception text: URLs carry keys
            status = getattr(getattr(exc, "response", None), "status_code", "")
            log.info(f"  [{engine}] search failed: {type(exc).__name__} {status}")
            continue
        fresh = [it for it in items if (it.get("link") or it.get("url")) and (it.get("link") or it.get("url")) not in seen]
        seen.update(it.get("link") or it.get("url") for it in fresh)
        for item in fresh:
            url = item.get("link") or item.get("url")
            job = gs._to_job(group, place, role, item, gs._page_text(sess, url))
            if job is None:
                continue
            if engine == "web":
                job.date_trusted = job.date_posted is not None   # web search is not limited to this week
            leads.append(Lead(job, ""))
        log.info(f"  [{engine}] {place}: {len(fresh)} results")
    return leads


def duckduckgo(sess, query: str) -> list[dict]:
    """DuckDuckGo HTML results, past week - the fallback when no SERP key is configured."""
    from bs4 import BeautifulSoup

    query = re.sub(r"\(site:[^)]*\)", "", query)       # DDG ignores long OR-ed site lists
    r = sess.get("https://html.duckduckgo.com/html/", params={"q": query, "df": "w"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for res in soup.select(".result"):
        a, snip = res.select_one("a.result__a"), res.select_one(".result__snippet")
        if a and a.get("href", "").startswith("http"):
            out.append({"link": a["href"], "title": a.get_text(" ", strip=True),
                        "snippet": snip.get_text(" ", strip=True) if snip else ""})
    time.sleep(random.uniform(2, 4))
    return out


def from_boards(cfg: dict, settings) -> list[Lead]:
    from jobspy import scrape_jobs

    src = cfg["sources"]["boards"]
    per, limit = int(src.get("per_query", 25)), int(src.get("max_queries", 10))
    hours = int(cfg["max_age_days"]) * 24
    plan = []
    for kw in cfg["keywords"][:4]:
        plan += [("indeed", kw, "India", False, "contract"), ("indeed", kw, "India", True, "parttime"),
                 ("linkedin", kw, "India", False, "contract"), ("linkedin", kw, "India", True, "contract")]
    plan += [("naukri", f"{kw} contract", None, False, None) for kw in cfg["keywords"][:2]]
    plan += [("naukri", f"{kw} freelance", None, False, None) for kw in cfg["keywords"][:1]]
    random.Random(datetime.now(IST).toordinal()).shuffle(plan)
    leads = []
    for site, kw, location, remote, jtype in plan[:limit]:
        kwargs = dict(site_name=[site], search_term=kw, results_wanted=per, hours_old=hours, is_remote=remote,
                      description_format="markdown", verbose=0)
        if location:
            kwargs["location"] = location
        if jtype:
            kwargs["job_type"] = jtype
        if site == "indeed":
            kwargs["country_indeed"] = "india"
        if site in ("linkedin", "naukri"):
            kwargs["fetch_description"] = True     # full text: HR emails, skills, contract terms
        try:
            df = scrape_jobs(**kwargs)
        except Exception as exc:  # noqa: BLE001 - one site down never stops the run
            log.info(f"  [{site}] '{kw}' failed: {type(exc).__name__}")
            continue
        q = boards.Query(site, kw, location, remote, "India", 2)
        rows = df.to_dict("records") if len(df) else []
        for row in rows:
            job = boards._to_job(row, q)
            declared = {"contract": "contract", "parttime": "part_time"}.get(jtype or "", "")
            leads.append(Lead(job, "", declared=declared or str(row.get("job_type") or "")))
        log.info(f"  [{site}] '{kw}' {'remote ' if remote else ''}{jtype or ''}: {len(rows)} postings")
        time.sleep(random.uniform(2, 5))
    return leads


def from_feeds(sess) -> list[Lead]:
    from autopilot.discovery import remote_feeds as rf

    leads = []
    try:
        data = sess.get("https://remotive.com/api/remote-jobs", params={"category": "devops", "limit": 100}).json()
        for j in data.get("jobs", []):
            if j.get("job_type") in ("contract", "freelance", "part_time"):
                leads.append(Lead(rf._job("remotive", j.get("title"), j.get("company_name"), j.get("url"),
                                          j.get("candidate_required_location"), j.get("description"),
                                          j.get("publication_date"), external_id=j.get("id")), "",
                                  declared=j["job_type"]))
    except Exception as exc:  # noqa: BLE001
        log.info(f"  [remotive] skipped: {type(exc).__name__}")
    try:
        data = sess.get("https://jobicy.com/api/v2/remote-jobs", params={"count": 50, "tag": "devops"}).json()
        for j in data.get("jobs", []):
            kinds = [str(t).lower() for t in (j.get("jobType") or [])]
            declared = next((k for k in kinds if k in ("contract", "part-time", "freelance")), "")
            if declared:
                leads.append(Lead(rf._job("jobicy", j.get("jobTitle"), j.get("companyName"), j.get("url"),
                                          j.get("jobGeo"), j.get("jobDescription") or j.get("jobExcerpt"),
                                          j.get("pubDate"), external_id=j.get("id")), "", declared=declared))
    except Exception as exc:  # noqa: BLE001
        log.info(f"  [jobicy] skipped: {type(exc).__name__}")
    try:
        data = sess.get("https://himalayas.app/jobs/api", params={"limit": 100, "q": "devops"}).json()
        for j in data.get("jobs", []):
            kind = str(j.get("employmentType") or "").lower()
            declared = "contract" if "contract" in kind else "part_time" if "part" in kind else \
                "freelance" if "freelance" in kind else ""
            if declared:
                where = ", ".join(j.get("locationRestrictions") or []) or "Remote (anywhere)"
                leads.append(Lead(rf._job("himalayas", j.get("title"), j.get("companyName"),
                                          j.get("applicationLink") or j.get("guid"), where, j.get("description"),
                                          j.get("pubDate"), external_id=j.get("guid")), "", declared=declared))
    except Exception as exc:  # noqa: BLE001
        log.info(f"  [himalayas] skipped: {type(exc).__name__}")
    log.info(f"  [feeds] {len(leads)} contract/freelance/part-time postings")
    return leads


# --------------------------------------------------------------------------- #
# Screening
# --------------------------------------------------------------------------- #
def screen(leads: list[Lead], cfg: dict, settings, profile, master: dict, history: dict) -> tuple[list[Lead], Counter]:
    from autopilot.resume.parser import resume_text
    from autopilot.screening import eligibility_reason, experience_reason, genuineness, skill_match, title_reason

    now = datetime.now(timezone.utc)
    resume_keys = implied(find_terms(resume_text(master)))
    cutoff = now - timedelta(days=int(cfg["max_age_days"]))
    seen_before = set(history.get("seen", {}))
    kept, why, urls, prints = [], Counter(), set(), set()
    for lead in leads:
        job = lead.job
        key = canonical_url(job.url)
        reason = ""
        if not job.url or key in urls or (job.hr_email or "") + job.fingerprint in prints:
            reason = "duplicate"
        elif key in seen_before:
            reason = "already in an earlier run"
        elif job.date_posted and job.date_posted < cutoff:
            reason = "older than max_age_days"
        elif not job.date_posted and not job.date_trusted:
            reason = "no posting date"
        elif title_reason(job.title, settings):
            reason = "title not a DevOps/Cloud role"
        if not reason:
            lead.kind = contract_type(job, lead.declared)
            if not lead.kind:
                reason = "not contract / freelance / part-time"
        if not reason:
            reason = genuineness(job, None, now) or experience_reason(job, settings)
        if not reason:
            reason = eligibility_reason(job, profile, settings)
            if not reason and not job.is_remote and (job.country or "India") != "India":
                reason = "on-site outside India"
        if not reason:
            job.score, job.matched, job.missing = skill_match(job, resume_keys)
            if len(set(job.matched) | set(job.missing)) >= 3 and job.score < float(cfg["min_match"]):
                reason = f"skill match {job.score:.0%} below {float(cfg['min_match']):.0%}"
        if reason:
            why[reason.split(" (")[0].split(":")[0]] += 1
            continue
        urls.add(key)
        prints.add((job.hr_email or "") + job.fingerprint)
        kept.append(lead)
    kept.sort(key=lambda lead: (not lead.job.hr_email, -lead.job.score))
    return kept, why


def summarize(leads: list[Lead], settings) -> None:
    """'Works / Description' in one line; Gemini/Groq/OpenRouter when available, else the post's first sentences.
    Only for your report - nothing an LLM writes here is sent to anyone."""
    from autopilot.tailoring.free_llm import QuickLLM

    llm = QuickLLM(settings, log.info)
    for lead in leads:
        lead.summary = brief(lead.job.description) or lead.job.title
    if not llm:
        return
    from concurrent.futures import ThreadPoolExecutor

    def ask(batch):
        listing = "\n\n".join(f"[{i}] {lead.job.title} at {lead.job.company or '?'}\n{lead.job.description[:1500]}"
                              for i, lead in enumerate(batch))
        return batch, llm.json("For each job post below, write what the work is in at most 25 plain words (the "
                               "project / responsibilities), using only what the post says. Reply as JSON "
                               '{"posts": [{"i": 0, "summary": "..."}]}\n\n' + listing) or {}

    started = time.time()
    batches = [leads[i:i + 10] for i in range(0, min(len(leads), 60), 10)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        answers = list(pool.map(ask, batches))
    log.info(f"Descriptions written by AI for {sum(len(b) for b, d in answers if d)} leads "
             f"({time.time() - started:.0f}s); the rest use the post's first sentences.")
    for batch, data in answers:
        for item in data.get("posts") or []:
            try:
                lead = batch[int(item.get("i"))]
            except (TypeError, ValueError, IndexError):
                continue
            text = re.sub(r"\s+", " ", str(item.get("summary") or "")).strip()
            if 5 <= len(text.split()) <= 40:
                lead.summary = text


# --------------------------------------------------------------------------- #
# Emails
# --------------------------------------------------------------------------- #
def contacted_before(history: dict, days: int) -> set[str]:
    """Addresses this agent, the daily job autopilot or the older mailers already emailed."""
    out = set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for email, when in history.get("contacted", {}).items():
        if (to_datetime(when) or cutoff) >= cutoff:
            out.add(email.lower())
    db = APP / "data" / "autopilot.db"
    if db.exists():
        import sqlite3

        con = sqlite3.connect(str(db))
        try:
            out |= {r[0].lower() for r in con.execute("SELECT email FROM contacts WHERE last_contacted >= ?",
                                                      (cutoff.isoformat(),))}
        except sqlite3.Error:
            pass
        finally:
            con.close()
    from autopilot.apply import existing_pipeline_addresses

    return out | existing_pipeline_addresses(load_settings())


def draft(lead: Lead, cfg: dict, profile, master: dict, out_dir: Path) -> SimpleNamespace:
    from autopilot.tailoring import email_template, rules

    job = lead.job
    weights = rules.jd_weights(job)
    from autopilot.resume.parser import resume_text

    master_keys = implied(find_terms(resume_text(master)))
    known = rules._company_known(job)
    intro = ("I'm reaching out about the {role} " + lead.kind.lower() + " opportunity{at}. I'm a {descriptor} "
             "with {years}+ years of hands-on experience in {skills}, and I'm available to start {join} on a "
             + lead.kind.lower() + " basis, remote or from India.")
    text, html = email_template.build(
        job, profile, master, rules.matched_display(master_keys, weights, 10), rules.descriptor(master), known,
        weights, intro=intro,
        extra_facts=(("Engagement", "Open to contract, freelance, part-time and project work"),))
    name = profile.name or ""
    subject = cfg["email"]["subject"].format(contract=lead.kind, title=email_template.display_title(job.title)
                                             or "DevOps Engineer", name=name, years=profile.years)
    folder = out_dir / "drafts" / f"{len(list((out_dir / 'drafts').glob('*'))) + 1:02d}-" \
                                  f"{re.sub(r'[^a-z0-9]+', '-', (job.company or 'lead').lower())[:30].strip('-')}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "email.txt").write_text(f"To: {job.hr_email}\nSubject: {subject}\n\n{text}", encoding="utf-8")
    (folder / "email.html").write_text(html, encoding="utf-8")
    lead.draft = folder
    resume = (HERE / cfg["email"]["resume"]).resolve()
    return SimpleNamespace(folder=folder, subject=subject, mail_text=text, mail_html=html, email_text=text,
                           email_html=html, resume_pdf=resume, attach_name=cfg["email"]["attachment_name"])


class Sender:
    def __init__(self):
        self.user, self.password = os.getenv("EMAIL_ADDRESS", ""), os.getenv("EMAIL_PASSWORD", "")
        self.host, self.port = os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "587"))
        self.smtp = None

    @property
    def ready(self) -> bool:
        return bool(self.user and self.password)

    def _connect(self):
        if self.smtp is None:
            self.smtp = smtplib.SMTP(self.host, self.port, timeout=30)
            self.smtp.ehlo()
            if os.getenv("SMTP_STARTTLS", "1") != "0":
                self.smtp.starttls(context=ssl.create_default_context())
                self.smtp.ehlo()
            self.smtp.login(self.user, self.password)
        return self.smtp

    def message(self, to: str, pkg, profile) -> EmailMessage:
        from email.utils import formataddr, make_msgid

        msg = EmailMessage()
        msg["From"] = formataddr((profile.name or "", self.user or profile.email or ""))
        msg["To"], msg["Subject"], msg["Date"] = to, pkg.subject, formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=(self.user or "localhost").split("@")[-1])
        if profile.email and profile.email.lower() != (self.user or "").lower():
            msg["Reply-To"] = profile.email
        msg.set_content(pkg.mail_text)
        msg.add_alternative(pkg.mail_html, subtype="html")
        if pkg.resume_pdf.exists():
            msg.add_attachment(pkg.resume_pdf.read_bytes(), maintype="application", subtype="pdf",
                               filename=pkg.attach_name)
        return msg

    def send(self, msg: EmailMessage) -> str:
        for attempt in (1, 2):
            try:
                self._connect().send_message(msg)
                return ""
            except smtplib.SMTPAuthenticationError as exc:
                raise RuntimeError(f"SMTP login failed ({exc.smtp_code}) - check EMAIL_PASSWORD") from exc
            except smtplib.SMTPRecipientsRefused:
                return "recipient refused"
            except (smtplib.SMTPServerDisconnected, ConnectionError, TimeoutError):
                self.smtp = None
                if attempt == 2:
                    return "SMTP connection lost"
            except smtplib.SMTPDataError as exc:
                if b"limit" in (exc.smtp_error or b"").lower():
                    raise RuntimeError("Gmail sending limit reached") from exc
                return f"SMTP error {exc.smtp_code}"
        return "not sent"

    def close(self):
        if self.smtp is not None:
            try:
                self.smtp.quit()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
# Excel + report
# --------------------------------------------------------------------------- #
def write_excel(leads: list[Lead], path: Path) -> Path:
    import pandas as pd
    from openpyxl.styles import Alignment, Font, PatternFill

    rows = [{"SR": i, "Company Name": lead.job.company or "Not stated", "Position": lead.job.title,
             "Works / Description": lead.summary, "Location": location_label(lead.job),
             "Salary / Rate": rate_of(lead.job), "Contract Type": lead.kind,
             "Contact / HR Email": lead.job.hr_email or "-", "Job Role": job_role(lead.job), "Status": lead.status}
            for i, lead in enumerate(leads, 1)]
    details = [{"SR": i, "Job post": lead.job.url, "Apply / contact link": lead.job.apply_url or lead.job.url,
                "Posted": lead.job.date_posted.astimezone(IST).strftime("%d %b %Y") if lead.job.date_posted else "",
                "Found on": lead.job.source, "Skill match %": round(lead.job.score * 100),
                "Matched skills": ", ".join(display(lead.job.matched[:10])),
                "Other emails": ", ".join(lead.job.alt_emails), "Note": lead.note,
                "Draft": str(lead.draft.relative_to(path.parent)) if lead.draft else ""}
               for i, lead in enumerate(leads, 1)]
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame(rows, columns=COLUMNS).to_excel(xw, sheet_name="Leads", index=False)
        pd.DataFrame(details).to_excel(xw, sheet_name="Details", index=False)
        widths = {"SR": 5, "Company Name": 24, "Position": 32, "Works / Description": 60, "Location": 22,
                  "Salary / Rate": 20, "Contract Type": 13, "Contact / HR Email": 30, "Job Role": 34, "Status": 15}
        ws = xw.sheets["Leads"]
        for col, name in enumerate(COLUMNS, 1):
            cell = ws.cell(1, col)
            cell.font, cell.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F4E78")
            ws.column_dimensions[cell.column_letter].width = widths[name]
        fills = {SENT: "C6EFCE", DRAFTED: "FFEB9C", REVIEW: "DDEBF7"}
        for r, lead in enumerate(leads, 2):
            for col in range(1, len(COLUMNS) + 1):
                ws.cell(r, col).alignment = Alignment(wrap_text=True, vertical="top")
            ws.cell(r, 3).hyperlink, ws.cell(r, 3).style = lead.job.url, "Hyperlink"
            ws.cell(r, 10).fill = PatternFill("solid", fgColor=fills.get(lead.status, "FFFFFF"))
        ws.freeze_panes, ws.auto_filter.ref = "A2", ws.dimensions
        xw.sheets["Details"].freeze_panes = "A2"
    return path


def email_report(xlsx: Path, leads: list[Lead], why: Counter, found: int, profile) -> None:
    from autopilot.report_mail import _table

    user, password = os.getenv("EMAIL_ADDRESS", ""), os.getenv("EMAIL_PASSWORD", "")
    to = os.getenv("REPORT_EMAIL") or profile.email or user
    if not (user and password and to):
        log.info("Report email skipped: EMAIL_ADDRESS / EMAIL_PASSWORD not set.")
        return
    c = Counter(lead.status for lead in leads)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Date"] = user, to, formatdate(localtime=True)
    msg["Subject"] = (f"Freelance leads {datetime.now(IST):%d %b %I:%M %p} IST - {len(leads)} leads, "
                      f"{c[SENT]} emailed, {c[DRAFTED]} drafted, {c[REVIEW]} to review")
    summary = (f"{found} postings checked, {len(leads)} genuine contract/freelance leads kept.\n"
               f"Sent {c[SENT]}, drafted {c[DRAFTED]}, pending review {c[REVIEW]}.\n"
               f"Dropped: {'; '.join(f'{k} {v}' for k, v in why.most_common(8))}\n\nThe workbook is attached.")
    msg.set_content(summary)
    table = _table(["SR", "Company", "Position", "Type", "Rate", "Location", "HR email", "Status", "Post"],
                   [[i, lead.job.company, lead.job.title, lead.kind, rate_of(lead.job), location_label(lead.job),
                     lead.job.hr_email or "-", lead.status, lead.job.url] for i, lead in enumerate(leads, 1)], {8})
    msg.add_alternative(f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#202124">'
                        f'<h2 style="margin:0 0 6px">Freelance / contract DevOps leads</h2>'
                        f'<p style="white-space:pre-line">{summary}</p>{table}</div>', subtype="html")
    msg.add_attachment(xlsx.read_bytes(), maintype="application",
                       subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=xlsx.name)
    s = Sender()
    try:
        s.send(msg)
        log.info(f"Report emailed to {to}")
    finally:
        s.close()


# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--send", action="store_true", help="email the HR contacts (default: draft only)")
    ap.add_argument("--max-send", type=int, help="override email.max_send")
    ap.add_argument("--no-report", action="store_true", help="do not email the workbook to yourself")
    ap.add_argument("--config", default=str(HERE / "config.yaml"), help="settings file (default: config.yaml)")
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    stamp = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
    run_dir = HERE / "runs" / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(run_dir)
    os.chdir(APP)
    load_env()
    settings, profile = load_settings(), load_profile()
    settings.data.setdefault("search", {})["max_age_days"] = int(cfg["max_age_days"])
    from autopilot.pipeline import get_master

    master = get_master(settings, log.info)
    hist_path = HERE / "data" / "history.json"
    history = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else {}
    log.info(f"Freelance lead run {stamp} - {'SEND' if args.send else 'draft only'}")

    sess = ThrottledSession()
    leads: list[Lead] = []
    if any(cfg["sources"][k].get("enabled", True) for k in ("google", "web", "duckduckgo")):
        leads += from_search(sess, cfg, settings)
    if cfg["sources"]["boards"].get("enabled", True):
        leads += from_boards(cfg, settings)
    if cfg["sources"]["feeds"].get("enabled", True):
        leads += from_feeds(sess)
    found = len(leads)
    from autopilot.tailoring.free_llm import QuickLLM

    ai_search.read_posts(QuickLLM(settings, log.info), [lead.job for lead in leads], log.info)
    leads = [lead for lead in leads if not lead.job.title.endswith("[not a hiring post]")]
    kept, why = screen(leads, cfg, settings, profile, master, history)
    log.info(f"{found} postings -> {len(kept)} genuine contract/freelance leads. Dropped: "
             + "; ".join(f"{k} {v}" for k, v in why.most_common(8)))
    summarize(kept, settings)

    done = contacted_before(history, int(cfg["email"]["recontact_after_days"]))
    sender = Sender()
    send = args.send and bool(cfg["email"].get("send", True))
    if send and not sender.ready:
        log.info("! EMAIL_ADDRESS / EMAIL_PASSWORD not set - drafting only")
        send = False
    cap, sent = args.max_send if args.max_send is not None else int(cfg["email"]["max_send"]), 0
    this_run: dict[str, int] = {}          # one email per HR address per run, even for two roles
    pause = cfg["email"].get("pause_seconds", [8, 20])
    try:
        for lead in kept:
            job = lead.job
            if not job.hr_email:
                lead.status, lead.note = REVIEW, "no HR email - apply via the link"
                continue
            if job.hr_email.lower() in done:
                lead.status, lead.note = REVIEW, "this address was already emailed (this agent or the job autopilot)"
                continue
            if job.hr_email.lower() in this_run:
                lead.status, lead.note = REVIEW, f"same HR address as lead {this_run[job.hr_email.lower()]} this run"
                continue
            this_run[job.hr_email.lower()] = kept.index(lead) + 1
            if not mail_domain_ok(job.hr_email.split("@")[-1]):
                lead.status, lead.note = REVIEW, "email domain cannot receive mail"
                continue
            pkg = draft(lead, cfg, profile, master, run_dir)
            lead.status = DRAFTED
            if not send or sent >= cap:
                lead.note = "draft only" if not send else f"over this run's cap of {cap}"
                continue
            problem = sender.send(sender.message(job.hr_email, pkg, profile))
            if problem:
                lead.note = problem
                continue
            lead.status, sent = SENT, sent + 1
            done.add(job.hr_email.lower())
            history.setdefault("contacted", {})[job.hr_email.lower()] = datetime.now(timezone.utc).isoformat()
            log.info(f"  sent: {job.title} @ {job.company or '?'}")
            time.sleep(random.uniform(float(pause[0]), float(pause[1])))
    except RuntimeError as exc:
        log.info(f"! sending stopped: {exc}")
    finally:
        sender.close()

    now = datetime.now(timezone.utc)
    seen = {k: v for k, v in history.get("seen", {}).items() if (to_datetime(v) or now) > now - timedelta(days=30)}
    if send:   # a draft-only run must not hide these leads from the next real run
        seen.update({canonical_url(lead.job.url): now.isoformat() for lead in kept})
    history["seen"] = seen
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    hist_path.write_text(json.dumps(history, indent=1), encoding="utf-8")

    xlsx = write_excel(kept, run_dir / f"devops_leads_{stamp}.xlsx")
    c = Counter(lead.status for lead in kept)
    log.info(f"Done: {len(kept)} leads - Sent {c[SENT]}, Email Drafted {c[DRAFTED]}, Pending Review {c[REVIEW]}")
    log.info(f"Excel: {xlsx}")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(f"### Freelance leads {stamp}\n- Postings checked: {found}\n- Leads kept: {len(kept)}\n"
                     f"- Sent: {c[SENT]} · Drafted: {c[DRAFTED]} · Pending review: {c[REVIEW]}\n"
                     f"- Full workbook: in your email inbox\n")
    if cfg.get("report_email", True) and not args.no_report:
        try:
            email_report(xlsx, kept, why, found, profile)
        except Exception as exc:  # noqa: BLE001 - the run itself succeeded
            log.info(f"! report email failed: {type(exc).__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
