"""Parsing helpers: pull emails, phones, experience, skills, company and
locations out of a raw block of job-post text, and classify emails by how
likely a real person is to reply.

These functions are pure (no network) so they are easy to test against the
sample posts in ``bulk_mail_sender/Pasted text(1).txt``.
"""

import re

import config

# --------------------------------------------------------------------------- #
# Emails
# --------------------------------------------------------------------------- #
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Local-parts that almost never get a useful hiring reply -> always rejected.
# Besides auto/no-reply addresses, this drops the EEO / accessibility / legal
# boilerplate inboxes that US job descriptions embed (not application contacts).
_REJECT_LOCAL = re.compile(
    r"^(no-?reply|do-?not-?reply|donotreply|mailer-?daemon|postmaster|"
    r"automated|notification|notifications|bounce|webmaster|abuse|support|"
    r"reasonable.?accommodation|accommodations?|accessibility|ada|eeo|"
    r"privacy|legal|press|media|investor|unsubscribe|compliance|security)|"
    r".*(fraud|scam|phishing|spam|donotapply)",  # anti-fraud / boilerplate inboxes
    re.IGNORECASE,
)

# Generic-but-still-reply-capable inboxes -> usable only as fallback.
# A trailing lookahead lets "hr", "hr4", "hr.team" all match while leaving
# names like "hrithik" classified as personal.
_GENERIC_LOCAL = re.compile(
    r"^(hr|hrd|hiring|career|careers|job|jobs|recruit|recruiter|recruiting|"
    r"recruitment|talent|talents|resume|cv|apply|application|info|contact|"
    r"team|office|admin|enquiry|enquiries|sales|hello)(?=[._\-0-9]|$)",
    re.IGNORECASE,
)

# Free-mail / placeholder domains we keep but don't over-trust.
_FREE_DOMAINS = {"example.com", "email.com", "domain.com", "yourcompany.com"}


def is_complete_email(email):
    """Reject truncated / malformed addresses.

    Google search snippets cut emails off with an ellipsis ("abhishe...@x.com"
    or "teena....@x.com"). Such an address can never receive mail, so we drop
    it -- and the job is skipped unless a *complete* email is found elsewhere.
    """
    if not email or "@" not in email:
        return False
    if "…" in email or ".." in email:        # ellipsis / truncation marker
        return False
    local, _, domain = email.partition("@")
    if not local or local.startswith(".") or local.endswith("."):
        return False
    if "." not in domain or domain.startswith(".") or domain.endswith("."):
        return False
    # A real local-part is rarely a single char left over from truncation.
    if len(local) < 2:
        return False
    return True


def extract_emails(text):
    """Return a de-duplicated, lowercased list of *complete* emails in *text*."""
    seen = []
    for raw in EMAIL_RE.findall(text or ""):
        email = raw.lower().strip(".")
        if not is_complete_email(email):
            continue
        if email not in seen:
            seen.append(email)
    return seen


def classify_email(email):
    """Return one of ``"personal"``, ``"generic"`` or ``"reject"``.

    * ``reject``  -> no-reply / automated / placeholder addresses.
    * ``generic`` -> hr@, careers@, recruiting@ ... (reply possible, last resort).
    * ``personal``-> looks like a named person (highest reply chance).
    """
    if "@" not in email:
        return "reject"
    local, _, domain = email.partition("@")

    if _REJECT_LOCAL.match(local):
        return "reject"
    if domain in _FREE_DOMAINS or domain.endswith(".png") or domain.endswith(".jpg"):
        return "reject"
    if _GENERIC_LOCAL.match(local):
        return "generic"
    return "personal"


def pick_emails(text):
    """Pick the best reply-capable HR email plus its alternates.

    Returns ``{"primary": str|None, "alternates": [str], "email_type": str}``.
    Personal addresses are always preferred over generic ones; rejected
    addresses (no-reply etc.) are dropped entirely.
    """
    personal, generic = [], []
    for email in extract_emails(text):
        kind = classify_email(email)
        if kind == "personal":
            personal.append(email)
        elif kind == "generic":
            generic.append(email)

    ordered = personal + generic
    if not ordered:
        return {"primary": None, "alternates": [], "email_type": None}

    primary = ordered[0]
    email_type = "personal" if primary in personal else "generic"
    alternates = [e for e in ordered[1:]]
    return {"primary": primary, "alternates": alternates, "email_type": email_type}


# --------------------------------------------------------------------------- #
# Phone numbers
# --------------------------------------------------------------------------- #
_PHONE_RES = [
    re.compile(r"\+91[\-\s]?\d{10}"),          # +91 98765 43210
    re.compile(r"\b91[\-\s]?[6-9]\d{9}\b"),    # 91 9876543210
    re.compile(r"\b[6-9]\d{9}\b"),             # 9876543210 (Indian mobile)
    re.compile(r"\+\d{1,3}[\-\s]?\d{6,12}"),   # generic international
]


def extract_phone(text):
    """Return the first plausible phone number in *text*, or ``""``."""
    for rx in _PHONE_RES:
        m = rx.search(text or "")
        if m:
            return m.group(0).strip()
    return ""


# --------------------------------------------------------------------------- #
# Experience
# --------------------------------------------------------------------------- #
# Matches "3-5 years", "3 to 5 years", "3+ years", "3 years", "3.5 yrs".
_EXP_RANGE = re.compile(
    r"(\d{1,2})(?:\.\d+)?\s*(?:-|–|—|to)\s*(\d{1,2})(?:\.\d+)?\s*\+?\s*(?:years|yrs|yr|year)",
    re.IGNORECASE,
)
_EXP_PLUS = re.compile(r"(\d{1,2})(?:\.\d+)?\s*\+\s*(?:years|yrs|yr|year)", re.IGNORECASE)
_EXP_SINGLE = re.compile(r"(\d{1,2})(?:\.\d+)?\s*(?:years|yrs|yr|year)", re.IGNORECASE)


def parse_experience(text):
    """Return ``(min, max)`` experience in years; ``max`` may be ``None``.

    Returns ``(None, None)`` when no experience is stated.
    """
    text = text or ""
    m = _EXP_RANGE.search(text)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return (min(lo, hi), max(lo, hi))
    m = _EXP_PLUS.search(text)
    if m:
        return (int(m.group(1)), None)
    m = _EXP_SINGLE.search(text)
    if m:
        return (int(m.group(1)), int(m.group(1)))
    return (None, None)


def format_experience(exp_min, exp_max):
    """Human-readable experience string for the output ('1-3 yrs', '3+ yrs')."""
    if exp_min is None and exp_max is None:
        return "Not specified"
    if exp_max is None:
        return f"{exp_min}+ yrs"
    if exp_min == exp_max:
        return f"{exp_min} yrs"
    return f"{exp_min}-{exp_max} yrs"


def experience_overlaps(exp_min, exp_max, want_min, want_max):
    """True if a job's [exp_min, exp_max] overlaps your [want_min, want_max].

    Unstated experience (``None, None``) is treated as a match (we keep it
    rather than discard a potentially good lead).
    """
    if exp_min is None and exp_max is None:
        return True
    lo = exp_min if exp_min is not None else 0
    hi = exp_max if exp_max is not None else 99
    return lo <= want_max and hi >= want_min


# --------------------------------------------------------------------------- #
# Skills
# --------------------------------------------------------------------------- #
def _skill_present(skill, low_text):
    """Whole-token match so 'ai' doesn't match 'email' or 'git' match 'digital'."""
    pattern = r"(?<![a-z0-9+#.])" + re.escape(skill) + r"(?![a-z0-9+#])"
    return re.search(pattern, low_text) is not None


def match_skills(text, candidate_skills=None):
    """Return the list of YOUR skills the post actually mentions (whole-word)."""
    candidate_skills = candidate_skills or config.CANDIDATE_SKILLS
    low = (text or "").lower()
    return [s for s in candidate_skills if _skill_present(s, low)]


def is_relevant(matched_skills):
    """True if the post is genuinely a DevOps/Cloud role worth keeping.

    Requires a CORE skill (so it's not just any job mentioning 'python') and at
    least MIN_SKILL_MATCHES skills overall. This drops the unwanted jobs.
    """
    core_hit = any(s in config.CORE_SKILLS for s in matched_skills)
    return core_hit and len(matched_skills) >= config.MIN_SKILL_MATCHES


_TITLE_TAIL = re.compile(r"\s*(?:[-|–—]\s.*|\bat\b\s.*|\(.*?\))$", re.IGNORECASE)


def clean_title(raw):
    """Turn a search/feed title into a short job title ('DevOps Engineer')."""
    if not raw:
        return ""
    title = raw.split("\n")[0].strip()
    # Drop trailing "- LinkedIn", "at Company", "| Naukri", parentheticals.
    title = _TITLE_TAIL.sub("", title).strip(" -|·")
    return title[:80]


# --------------------------------------------------------------------------- #
# Company & locations
# --------------------------------------------------------------------------- #
_COMPANY_LINE = re.compile(r"company\s*[:\-]\s*([A-Za-z0-9&.,'\- ]{2,60})", re.IGNORECASE)


def guess_company(text, email=None, fallback=None):
    """Best-effort company name from a 'Company:' line, else the email domain."""
    m = _COMPANY_LINE.search(text or "")
    if m:
        return m.group(1).strip().rstrip(".")
    if email and "@" in email:
        domain = email.split("@", 1)[1]
        host = domain.split(".")[0]
        if host and host not in {"gmail", "yahoo", "outlook", "hotmail", "rediffmail"}:
            return host.replace("-", " ").title()
    return fallback or ""


def guess_locations(text, limit=3):
    """Return up to *limit* matched locations, comma-joined, in priority order.

    Capped because portal pages list many regions in sidebars; the priority
    ordering keeps the most relevant (Remote + top target cities) first.
    """
    low = (text or "").lower()
    found = []
    for loc in config.LOCATION_PRIORITY:
        if loc in low:
            label = "Remote" if loc in {"remote", "work from home", "wfh"} else loc.title()
            if label not in found:
                found.append(label)
        if len(found) >= limit:
            break
    return ",".join(found)


def location_rank(locations_str):
    """Lower number = higher priority. Used for sorting leads."""
    low = (locations_str or "").lower()
    for i, loc in enumerate(config.LOCATION_PRIORITY):
        if loc in low:
            return i
    return len(config.LOCATION_PRIORITY) + 1
