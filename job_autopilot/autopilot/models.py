"""The Job record that flows through discover -> screen -> tailor -> apply."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

# How a job can be applied to. A job with an HR email can always go by email,
# whatever its apply_type says.
APPLY_EMAIL = "email"
APPLY_GREENHOUSE = "greenhouse"
APPLY_LEVER = "lever"
APPLY_ASHBY = "ashby"
APPLY_LINKEDIN = "linkedin_easy_apply"
APPLY_LINKEDIN_OFFSITE = "linkedin_offsite"
APPLY_NAUKRI = "naukri"
APPLY_EXTERNAL = "external"

ATS_TYPES = (APPLY_GREENHOUSE, APPLY_LEVER, APPLY_ASHBY)

# Job.status values stored in the database.
ST_NEW = "new"
ST_REJECTED = "rejected"
ST_QUEUED = "queued"
ST_APPLIED = "applied"
ST_MANUAL = "manual"              # needs you: open the link and apply
ST_ATTENTION = "needs_attention"  # bot started, got stuck (captcha, unknown question)
ST_FAILED = "failed"


def canonical_url(url: str) -> str:
    """Drop query strings and fragments so tracking params don't defeat dedupe."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_COMPANY_SUFFIX = re.compile(
    r"\b(private|pvt|limited|ltd|llp|llc|inc|incorporated|corp|corporation|co|gmbh|plc|"
    r"technologies|technology|solutions|services|software|systems|labs|india)\b"
)
_TITLE_NOISE = re.compile(r"\b(i|ii|iii|iv|l1|l2|l3|sr|senior|junior|jr|mid|level)\b")


def norm_company(name: str) -> str:
    low = _COMPANY_SUFFIX.sub(" ", (name or "").lower())
    return _NON_ALNUM.sub(" ", low).strip()


def norm_title(title: str) -> str:
    low = re.sub(r"\(.*?\)", " ", (title or "").lower())
    low = _NON_ALNUM.sub(" ", low)
    return re.sub(r"\s+", " ", _TITLE_NOISE.sub(" ", low)).strip()


@dataclass
class Job:
    source: str
    title: str
    company: str
    url: str
    location: str = ""
    description: str = ""
    apply_url: str = ""
    apply_type: str = APPLY_EXTERNAL
    hr_email: str = ""
    alt_emails: list[str] = field(default_factory=list)
    date_posted: datetime | None = None
    date_trusted: bool = False      # the source already filtered by posting age
    is_remote: bool = False
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = ""
    salary_interval: str = ""       # yearly | monthly | weekly | daily | hourly
    experience_text: str = ""
    external_id: str = ""

    # Filled in by screening.
    tier: int = 0
    score: float = 0.0
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    reject_reason: str = ""
    country: str = ""
    applicants: int | None = None   # LinkedIn's "Over 200 applicants" etc.
    chance: float = 0.0             # hiring-chance score 0-1 (see screening.hiring_chance)

    @property
    def id(self) -> str:
        key = f"{self.source}:{self.external_id or canonical_url(self.url)}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]

    @property
    def fingerprint(self) -> str:
        """Same role at the same company, whichever board it came from."""
        return f"{norm_company(self.company)}|{norm_title(self.title)}"

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.company}\n{self.location}\n{self.description}"

    def reject(self, reason: str) -> "Job":
        self.reject_reason = reason
        return self
