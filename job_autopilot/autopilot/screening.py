"""Decide which postings are worth applying to, and in what order.

Cheap checks run first (title, freshness, already-applied), then duplicates
across boards are merged, LinkedIn pages are read for the survivors, and the
expensive checks follow: genuineness, eligibility, experience and skill match.
Every rejection keeps its reason, so `autopilot status` can show why a job
was skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from autopilot.config import Profile, Settings
from autopilot.db import Database
from autopilot.models import (APPLY_ASHBY, APPLY_EMAIL, APPLY_GREENHOUSE, APPLY_LEVER, APPLY_LINKEDIN,
                              APPLY_LINKEDIN_OFFSITE, APPLY_NAUKRI, ATS_TYPES, Job, norm_company)
from autopilot.resume.parser import resume_text
from autopilot.text import (CORE_TERMS, WEIGHT, detect_countries, detect_country, find_terms, implied,
                            parse_experience)

# --------------------------------------------------------------------------- #
# Title
# --------------------------------------------------------------------------- #
def _title_norm(text: str) -> str:
    # Punctuation becomes spaces on both sides: "(SRE)" matches "sre", "Pre-Sales" matches "pre-sales".
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+#.]+", " ", (text or "").lower())).strip()


def _has_words(title_norm: str, phrase: str) -> bool:
    """Whole-word match: 'cloud' is in 'Cloud Engineer' but not in 'Cloudflare'."""
    words = _title_norm(phrase)
    return bool(words) and re.search(rf"(?<![a-z0-9]){re.escape(words)}(?![a-z0-9])", title_norm) is not None


def title_reason(title: str, settings: Settings) -> str:
    low = _title_norm(title)
    include = settings.get("search.title_include", [])
    if include and not any(_has_words(low, word) for word in include):
        return "title not a DevOps/Cloud role"
    for word in settings.get("search.title_exclude", []):
        if _has_words(low, word):
            return f"title contains '{word.strip()}'"
    return ""


# --------------------------------------------------------------------------- #
# Freshness
# --------------------------------------------------------------------------- #
def freshness_reason(job: Job, settings: Settings, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    max_days = int(settings.get("search.max_age_days", 7))
    if job.date_posted is None:
        if settings.get("search.drop_undated", True) and not job.date_trusted:
            return "no posting date (can't prove it is fresh)"
        return ""
    age = (now - job.date_posted).total_seconds() / 86400
    if age > max_days + 0.99:   # boards report whole days; allow the partial day
        return f"posted {int(age)} days ago (limit {max_days})"
    return ""


# --------------------------------------------------------------------------- #
# Genuineness
# --------------------------------------------------------------------------- #
SCAM_PHRASES = [
    "registration fee", "registration charges", "security deposit", "refundable deposit",
    "training fee", "pay for training", "processing fee", "advance payment", "investment required",
    "earn per day", "earn daily", "work from home and earn", "data entry job", "typing job",
    "whatsapp only", "telegram only", "contact on telegram", "without any interview",
    "direct joining without interview", "job guarantee", "100% placement", "placement guarantee",
    "pay after placement", "network marketing", "multi level marketing", "multi-level marketing",
    "commission only", "course fee", "job oriented course", "fees applicable", "pay to apply",
]
PLACEHOLDER_COMPANIES = {"", "confidential", "n a", "na", "company", "hiring", "urgent hiring",
                         "stealth", "stealth startup", "undisclosed", "leading company", "mnc"}
GHOST_FLAG_DAYS = 30
GHOST_REJECT_DAYS = 60


def genuineness(job: Job, db: Database | None, now: datetime) -> str:
    """Reject reason for scams/closed/ghost postings; softer issues become flags."""
    low = job.text.lower()
    for phrase in SCAM_PHRASES:
        if phrase in low:
            return f"scam signal: '{phrase}'"
    if "closed" in job.flags:
        return "no longer accepting applications"
    if any(f.startswith("seniority:") for f in job.flags):
        return "LinkedIn " + next(f for f in job.flags if f.startswith("seniority:")).replace(":", " level ")
    email_lead = job.source in ("hr-leads", "pasted")
    if not email_lead and norm_company(job.company) in PLACEHOLDER_COMPANIES:
        job.flags.append("unnamed company")
    if not job.description.strip():
        job.flags.append("no description")
    elif len(job.description) < 200 and not email_lead:
        job.flags.append("thin description")
    if job.hr_email:
        from autopilot.discovery.common import mail_domain_ok

        if not mail_domain_ok(job.hr_email.split("@")[-1]):
            job.flags.append(f"dropped {job.hr_email}: domain cannot receive mail")
            job.hr_email = job.alt_emails.pop(0) if job.alt_emails else ""
            if not job.hr_email and job.apply_type == "email":
                return "HR email domain cannot receive mail"
    if db is not None:
        first = db.first_seen_for_fingerprint(job.fingerprint)
        if first:
            days = (now - first).days
            if days >= GHOST_REJECT_DAYS:
                return f"same role re-posted for {days} days (likely ghost job)"
            if days >= GHOST_FLAG_DAYS:
                job.flags.append(f"re-posted for {days} days")
    return ""


# --------------------------------------------------------------------------- #
# Eligibility (work authorisation / location)
# --------------------------------------------------------------------------- #
_RESTRICTIONS = [
    ("United States", re.compile(
        r"\b(?:us|u\.s\.|usa|united states)[\s-]*(?:only|based)\b|remote\s*[-–(,/]\s*(?:us|usa|u\.s\.|united states)\b|"
        r"(?:located|based|reside|residing|live)\s+(?:in|within)\s+(?:the\s+)?(?:us|usa|u\.s\.|united states)\b|"
        r"authori[sz]ed to work in the (?:us|u\.s\.|united states)|\bu\.?s\.? citizen|green card|"
        r"security clearance|clearance required|ts/sci|public trust clearance", re.I)),
    ("United Kingdom", re.compile(r"right to work in the (?:uk|united kingdom)|eligible to work in the uk|"
                                  r"\buk[\s-]*(?:only|based)\b", re.I)),
    ("Europe", re.compile(r"(?:eu|european union) work permit|eligible to work in the (?:eu|european union)|"
                          r"\beu[\s-]*(?:only|based)\b|(?:located|based) in (?:the )?(?:eu|europe)\b", re.I)),
    ("Canada", re.compile(r"canada[\s-]*(?:only|based)|eligible to work in canada|located in canada", re.I)),
    ("LATAM", re.compile(r"(?:located|based) in (?:latam|latin america)", re.I)),
]
_ANYWHERE = re.compile(r"work from anywhere|anywhere in the world|worldwide|globally remote|global remote|"
                       r"fully distributed|any time ?zone|remote[\s-]*(?:global|anywhere)", re.I)
_NO_SPONSOR = re.compile(r"(?:no|not|unable to|cannot|can't|won't|will not|do not|does not)\s+(?:provide\s+|"
                         r"offer\s+)?(?:visa\s+)?sponsor|sponsorship (?:is )?not (?:available|provided|offered)|"
                         r"without (?:the need for )?(?:visa )?sponsorship", re.I)


def is_remote(job: Job) -> bool:
    low = f"{job.title} {job.location}".lower()
    return job.is_remote or "remote" in low or "work from home" in low or "wfh" in low


_PLACE_NOISE = re.compile(r"\b(?:remote|hybrid|on-?site|onsite|work from home|wfh|anywhere|flexible|"
                          r"multiple locations?|various|location|locations|area|metropolitan|region)\b|[()|/,;:\-–]", re.I)


def eligibility_reason(job: Job, profile: Profile, settings: Settings) -> str:
    authorized = {c.lower() for c in (profile.authorized_countries or ["India"])}
    regions = list(detect_countries(job.location))
    if not regions and job.country:
        regions = [job.country]           # e.g. an Indeed India search: the domain fixes the country
    open_to_you = lambda r: r in ("Worldwide", "APAC") or r.lower() in authorized  # noqa: E731
    text = f"{job.location}\n{job.description}"
    named_place = _PLACE_NOISE.sub(" ", job.location or "").strip()
    if is_remote(job):
        if regions:
            # The location field is the employer's own statement of who may apply:
            # "EMEA, LATAM, Canada, USA" rules India out whatever the prose says.
            if any(open_to_you(r) for r in regions):
                return ""
            return f"remote, but only for {', '.join(regions)}"
        if named_place:
            return f"remote, but based in {job.location.strip()}"
        for region, rx in _RESTRICTIONS:
            if region.lower() not in authorized and rx.search(text):
                return f"remote, but only for people in {region}"
        return ""
    if not regions:
        # "Hybrid" / blank location: look for the office in the opening of the description.
        regions = list(detect_countries(job.description[:800]))
        if not regions:
            return "on-site/hybrid with no stated country"
    if any(r.lower() in authorized for r in regions):
        return ""
    country = regions[0]
    targets = {detect_country(p) for p in settings.get("locations.international.places", [])}
    if not settings.get("locations.international.enabled", True) or country not in targets:
        return f"on-site in {country or 'an unknown location'} (not a target location)"
    if _NO_SPONSOR.search(job.description):
        return "no visa sponsorship"
    for region, rx in _RESTRICTIONS:
        if region == country and rx.search(text):
            return f"requires existing work authorisation in {region}"
    return ""


# --------------------------------------------------------------------------- #
# Experience and skill match
# --------------------------------------------------------------------------- #
def experience_reason(job: Job, settings: Settings) -> str:
    years = int(settings.get("search.experience_years", 3))
    stretch = int(settings.get("search.experience_stretch", 1))
    lo, hi = parse_experience(job.experience_text, explicit=True) if job.experience_text else (None, None)
    if lo is None:
        lo, hi = parse_experience(job.description)
    if lo is not None and lo > years + stretch:
        return f"asks for {lo}+ years (you have {years})"
    if hi is not None and hi < years - 1:
        return f"too junior ({lo}-{hi} years)"
    return ""


def skill_match(job: Job, resume_keys: set[str]) -> tuple[float, list[str], list[str]]:
    """Share of the posting's (weighted) tech requirements the resume covers."""
    jd = find_terms(f"{job.title}\n{job.description}")
    if not jd:
        return 0.0, [], []
    in_title = find_terms(job.title)
    weights = {k: WEIGHT.get(k, 1) * (1 + 0.25 * min(n - 1, 2)) * (1.5 if k in in_title else 1)
               for k, n in jd.items()}
    total = sum(weights.values())
    matched = sorted((k for k in jd if k in resume_keys), key=lambda k: -weights[k])
    missing = sorted((k for k in jd if k not in resume_keys), key=lambda k: -weights[k])
    return sum(weights[k] for k in matched) / total, matched, missing


# --------------------------------------------------------------------------- #
# Priority tier and pay
# --------------------------------------------------------------------------- #
USD_RATE = {"USD": 1.0, "INR": 0.012, "EUR": 1.08, "GBP": 1.27, "SGD": 0.74, "AED": 0.27, "JPY": 0.0067,
            "CAD": 0.73, "AUD": 0.66, "CHF": 1.12, "NZD": 0.6, "PLN": 0.25, "SEK": 0.095}
PER_YEAR = {"yearly": 1, "monthly": 12, "weekly": 52, "daily": 260, "hourly": 2080, "": 1}


def annual_usd(job: Job) -> float | None:
    top = job.salary_max or job.salary_min
    rate = USD_RATE.get((job.salary_currency or "USD").upper())
    if not top or rate is None:
        return None
    return float(top) * PER_YEAR.get(job.salary_interval or "", 1) * rate


def tier_of(job: Job, settings: Settings) -> tuple[int, str]:
    pay = annual_usd(job)
    country = job.country or detect_country(job.location)
    if is_remote(job):
        floor = float(settings.get("locations.remote.min_annual_usd", 0) or 0)
        if pay is not None and pay < floor:
            if country == "India":
                return 2, ""   # an Indian remote role at Indian pay is still a good India role
            return 0, f"remote pay about ${pay:,.0f}/yr is below your ${floor:,.0f} floor"
        return 1, ""
    if country == "India" or (not country and job.source in ("naukri", "hr-leads", "pasted")):
        return 2, ""
    return 3, ""


# --------------------------------------------------------------------------- #
# Duplicates across boards
# --------------------------------------------------------------------------- #
# Lower = better route to the same job: the employer's own form, then a
# recruiter's inbox, then on-board applies, then a bare link.
_ROUTE = {APPLY_GREENHOUSE: 0, APPLY_LEVER: 0, APPLY_ASHBY: 0, APPLY_EMAIL: 1, APPLY_LINKEDIN: 2,
          APPLY_NAUKRI: 3, APPLY_LINKEDIN_OFFSITE: 4}


def _route_rank(job: Job) -> tuple[int, int]:
    rank = _ROUTE.get(job.apply_type, 5)
    if job.hr_email and rank > 1:
        rank = 1
    return rank, -len(job.description)


def dedupe(jobs: list[Job]) -> tuple[list[Job], list[Job]]:
    groups: dict[str, list[Job]] = {}
    for job in jobs:
        key = job.fingerprint if job.company and job.title else job.id
        if job.hr_email:
            key += "|" + job.hr_email.lower()     # different recruiters = different openings
        groups.setdefault(key, []).append(job)
    kept, dropped = [], []
    for group in groups.values():
        group.sort(key=_route_rank)
        best = group[0]
        for other in group[1:]:
            if len(other.description) > len(best.description):
                best.description = other.description          # keep the fullest JD
            if not best.hr_email and other.hr_email:
                best.hr_email, best.alt_emails = other.hr_email, other.alt_emails
            if best.date_posted is None and other.date_posted is not None:
                best.date_posted, best.date_trusted = other.date_posted, other.date_trusted
            dropped.append(other.reject(f"duplicate of the {best.source} posting"))
        kept.append(best)
    return kept, dropped


# --------------------------------------------------------------------------- #
# The whole screen
# --------------------------------------------------------------------------- #
@dataclass
class ScreenResult:
    accepted: list[Job] = field(default_factory=list)
    rejected: list[Job] = field(default_factory=list)


def _cheap_reason(job: Job, settings: Settings, db: Database | None, now: datetime) -> str:
    reason = title_reason(job.title, settings) or freshness_reason(job, settings, now)
    if reason:
        return reason
    company = norm_company(job.company)
    for avoid in settings.get("search.avoid_companies", []):
        if company and norm_company(avoid) and norm_company(avoid) in company:
            return f"company on your avoid list ({avoid})"
    low = job.text.lower()
    for phrase in settings.get("search.exclude_phrases", []):
        if phrase.lower() in low:
            return f"contains excluded phrase '{phrase}'"
    return db.handled(job) if db is not None else ""


def hiring_chance(job: Job, settings: Settings, now: datetime) -> float:
    """0-1 score for "worth applying first": fit, freshness, competition, how direct the route is.

    It ranks jobs; it is not a probability. Weights: skill match 40%, freshness 20%,
    competition (LinkedIn applicant count) 15%, application route 15%, experience fit 10%.
    """
    age = (now - job.date_posted).days if job.date_posted else 4
    fresh = max(0.2, 1 - age / 8)
    n = job.applicants
    competition = 0.6 if n is None else 1.0 if n <= 25 else 0.75 if n <= 100 else 0.55 if n <= 200 else 0.35
    if job.hr_email:
        from autopilot.text import classify_email

        route = 1.0 if classify_email(job.hr_email) == "personal" else 0.8
    else:
        route = {APPLY_GREENHOUSE: 0.75, APPLY_LEVER: 0.75, APPLY_ASHBY: 0.75, APPLY_LINKEDIN: 0.65,
                 APPLY_NAUKRI: 0.6}.get(job.apply_type, 0.45)
    years = int(settings.get("search.experience_years", 3))
    lo, _ = parse_experience(job.experience_text, explicit=True) if job.experience_text else (None, None)
    if lo is None:
        lo, _ = parse_experience(job.description)
    fit = 0.8 if lo is None else 1.0 if lo <= years else 0.6
    return round(0.4 * job.score + 0.2 * fresh + 0.15 * competition + 0.15 * route + 0.1 * fit, 3)


def rank_key(job: Job, now: datetime) -> tuple:
    bonus = 0.0
    if job.date_posted and now - job.date_posted < timedelta(days=2):
        bonus += 0.05
    if job.hr_email or job.apply_type in ATS_TYPES:
        bonus += 0.05
    if (annual_usd(job) or 0) > 0:
        bonus += 0.03
    bonus -= 0.05 * len([f for f in job.flags if not f.startswith(("re-posted", "dropped"))])
    return (job.tier, -(job.chance + bonus))


def screen(jobs: list[Job], settings: Settings, profile: Profile, master: dict, db: Database | None,
           log=print, enrich_linkedin: bool = True) -> ScreenResult:
    now = datetime.now(timezone.utc)
    result = ScreenResult()
    resume_keys = implied(find_terms(resume_text(master)))
    min_match = float(settings.get("search.min_match", 0.3))

    survivors = []
    for job in jobs:
        reason = _cheap_reason(job, settings, db, now)
        (result.rejected if reason else survivors).append(job.reject(reason) if reason else job)
    survivors, dupes = dedupe(survivors)
    result.rejected += dupes

    if enrich_linkedin:
        from autopilot.discovery.linkedin_details import enrich_all
        enrich_all(survivors, int(settings.get("sources.linkedin.enrich_limit", 40)), log)

    for job in survivors:
        if job.source == "linkedin" and not job.description and enrich_linkedin:
            # Beyond the page-read budget: no JD means no honest screening or tailoring.
            # It stays un-applied, so a later run (with budget left) can pick it up.
            result.rejected.append(job.reject("LinkedIn page not read this run (budget/rate limit)"))
            continue
        reason = (genuineness(job, db, now) or eligibility_reason(job, profile, settings)
                  or experience_reason(job, settings))
        if not reason:
            job.score, job.matched, job.missing = skill_match(job, resume_keys)
            jd_terms = set(job.matched) | set(job.missing)
            if job.description and len(jd_terms) < 3:
                job.score = max(job.score, 0.5)       # too little text to judge; don't punish
                job.flags.append("few tech details")
            if jd_terms and not (jd_terms & CORE_TERMS):
                reason = "not a DevOps/Cloud role (no core skills mentioned)"
            elif job.description and len(jd_terms) >= 3 and job.score < min_match:
                reason = f"skill match {job.score:.0%} is below {min_match:.0%}"
        if not reason:
            job.tier, reason = tier_of(job, settings)
            job.chance = hiring_chance(job, settings, now)
        (result.rejected if reason else result.accepted).append(job.reject(reason) if reason else job)

    result.accepted.sort(key=lambda j: rank_key(j, now))
    if db is not None:
        for job in result.rejected:
            db.upsert_job(job, status="rejected", reason=job.reject_reason)
        for job in result.accepted:
            db.upsert_job(job, status="queued")
    return result
