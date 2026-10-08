"""Answer application-form questions - from your profile, from evidence in
your resume, and from answers you gave before - and ask you once when none of
those knows. Every answer you type is stored, so it is never asked again.

Truthfulness rules baked in:
* "Years of experience with X" comes from which roles' bullets mention X
  (or your skill_years override) - never a blanket number.
* "Do you have experience with X?" is Yes only if X is in your resume.
* Identity documents, date of birth etc. are never guessed.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from dateutil import parser as date_parser

from autopilot.config import Profile
from autopilot.db import Database, question_key
from autopilot.models import Job
from autopilot.text import DISPLAY, detect_country, find_terms, implied

YES, NO = "Yes", "No"
_DECLINE = re.compile(r"decline|prefer not|don.?t wish|do not wish|not to (?:say|answer|disclose|self)|rather not|"
                      r"choose not|not specified|undisclosed", re.I)


@dataclass
class Question:
    label: str
    kind: str = "text"            # text, textarea, number, email, tel, url, select, radio, checkbox, checkbox_group, combobox
    options: list[str] = field(default_factory=list)
    required: bool = False

    @property
    def clean_label(self) -> str:
        return re.sub(r"\s*[*✱]\s*$", "", re.sub(r"\s+", " ", self.label or "")).strip()


# --------------------------------------------------------------------------- #
# Picking a form option for an answer
# --------------------------------------------------------------------------- #
_YES_WORDS = re.compile(r"^(yes|y|true|i do|i am|i have|i agree|agree|i accept|accept|i consent|consent|"
                        r"i confirm|confirm|i will|i can|sure|absolutely)\b", re.I)
_NO_WORDS = re.compile(r"^(no|n|false|i do not|i don.?t|i am not|i have not|not)\b", re.I)
_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*\+|"
                    r"(?:less than|under|<)\s*(\d+(?:\.\d+)?)|(?:more than|over|above|>)\s*(\d+(?:\.\d+)?)", re.I)
_PLACEHOLDER_OPTION = re.compile(r"^(select|choose|please select|--|—|-)\b|^$", re.I)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9+]+", " ", (text or "").lower()).strip()


def _number_in(option: str, value: float, half_open: bool = False) -> bool:
    for m in _RANGE.finditer(option):
        lo, hi, plus, under, over = m.groups()
        if lo and hi and float(lo) <= value and (value < float(hi) if half_open else value <= float(hi)):
            return True
        if plus and value >= float(plus):
            return True
        if under and value < float(under):
            return True
        if over and value > float(over):
            return True
    return False


def choose_option(answer: str, options: list[str]) -> str | None:
    """The option that best expresses *answer* ('Yes', '3', 'Prefer not to say'...)."""
    real = [o for o in options if o and not _PLACEHOLDER_OPTION.match(o.strip())]
    if not real:
        return None
    ans = (answer or "").strip()
    na = _norm(ans)
    for o in real:
        if _norm(o) == na:
            return o
    if _DECLINE.search(ans):
        return next((o for o in real if _DECLINE.search(o)), None)
    if _YES_WORDS.match(ans):
        return next((o for o in real if _YES_WORDS.match(o.strip())), None)
    if _NO_WORDS.match(ans):
        return next((o for o in real if _NO_WORDS.match(o.strip())), None)
    number = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(?:lpa|lakhs?|years?|yrs?|days?|months?|k)?\b", ans, re.I)
    if number and any(_RANGE.search(o) for o in real):
        value = float(number.group(1))
        # "3" picks "3-5 Years" over "2-3 Years": a value on a boundary belongs to the range it starts.
        hit = next((o for o in real if _number_in(o, value, half_open=True)), None) or \
            next((o for o in real if _number_in(o, value)), None)
        if hit:
            return hit
    if number:
        exact = next((o for o in real if re.search(rf"(?<!\d){re.escape(number.group(1))}(?!\d)", o)), None)
        if exact:
            return exact
    for o in real:
        no = _norm(o)
        if len(na) >= 3 and (na in no or (len(no) >= 3 and no in na)):
            return o
    best = max(real, key=lambda o: difflib.SequenceMatcher(None, na, _norm(o)).ratio())
    return best if difflib.SequenceMatcher(None, na, _norm(best)).ratio() >= 0.6 else None


# --------------------------------------------------------------------------- #
# Evidence-based skill years
# --------------------------------------------------------------------------- #
def _months(dates: str, today: date | None = None) -> int:
    today = today or date.today()
    parts = re.split(r"\s*(?:-|–|—|\bto\b)\s*", dates or "", maxsplit=1)
    if len(parts) != 2:
        return 0
    try:
        start = date_parser.parse(parts[0], default=datetime(2000, 1, 1)).date()
        end = today if re.search(r"present|current|now|till", parts[1], re.I) else \
            date_parser.parse(parts[1], default=datetime(2000, 1, 1)).date()
    except (ValueError, OverflowError):
        return 0
    return max(0, (end.year - start.year) * 12 + end.month - start.month + 1)


def skill_years_from_resume(master: dict) -> dict[str, float]:
    """Years per skill = total length of the roles whose bullets mention it.
    Skills that appear only in the skills list or projects count as 1 year."""
    months: dict[str, int] = {}
    for role in master.get("experience", []):
        span = _months(role.get("dates", ""))
        text = " ".join([role.get("title", "")] + [f"{b.get('label', '')} {b['text']}" for b in role["bullets"]])
        for key in implied(find_terms(text)):
            months[key] = months.get(key, 0) + span
    from autopilot.resume.parser import resume_text

    years = {k: round(m / 12, 1) for k, m in months.items()}
    for key in implied(find_terms(resume_text(master))):
        years.setdefault(key, 1.0)
    return years


# --------------------------------------------------------------------------- #
# The answer book
# --------------------------------------------------------------------------- #
class AnswerBook:
    def __init__(self, db: Database, profile: Profile, master: dict, interactive: bool, log=print):
        self.db, self.p, self.master, self.interactive, self.log = db, profile, master, interactive, log
        self.evidence_years = skill_years_from_resume(master)
        self.overrides = {k.lower(): v for k, v in (profile.skill_years or {}).items()}
        self.past_employers = [r.get("company", "").lower() for r in master.get("experience", []) if r.get("company")]
        self.cover_text = ""
        self.source_label = ""

    # -- helpers ------------------------------------------------------------
    def _job_country(self, job: Job) -> str:
        return job.country or detect_country(job.location) or "India"

    def _authorized(self, job: Job) -> bool:
        allowed = {c.lower() for c in (self.p.authorized_countries or ["India"])}
        country = self._job_country(job)
        return country.lower() in allowed or (country in ("Worldwide", "APAC") and "india" in allowed)

    def skill_years(self, skill_text: str) -> str:
        keys = list(find_terms(skill_text))
        if not keys:
            return ""                  # not a skill we can judge -> ask
        best = 0.0
        for key in keys:
            override = self.overrides.get(key) or self.overrides.get(DISPLAY.get(key, key).lower())
            value = float(override) if override is not None else self.evidence_years.get(key, 0.0)
            best = max(best, value)
        best = min(best, float(self.p.years or best))
        return str(int(best + 0.5)) if best >= 1 else ("1" if best > 0 else "0")

    def _salary(self, job: Job, current: bool) -> str:
        if current:
            return str(self.p.current_ctc or "")
        if self._job_country(job) == "India":
            return str(self.p.expected_ctc or "")
        return str(self.p.expected_salary_usd or self.p.expected_ctc or "")

    def _notice(self, q: Question) -> str:
        if q.kind == "number":
            days = self.p.notice_period_days
            if days is None and str(self.p.notice_period or "").lower().startswith("immediate"):
                days = 0
            return "" if days is None else str(days)
        return str(self.p.notice_period or "")

    # -- the rules -----------------------------------------------------------
    def _rule_answer(self, q: Question, job: Job) -> str | None:
        t = q.clean_label.lower()
        p = self.p
        rules = [
            (r"\b(date of birth|dob|birth ?date|ssn|social security|national id|aadh?aa?r|pan (?:card|number)|"
             r"passport|driver.?s licen[sc]e)", lambda: None),
            (r"first name|given name|forename", lambda: p.first_name),
            (r"last name|surname|family name", lambda: p.last_name),
            (r"preferred name", lambda: p.first_name),
            (r"^(?:your )?(?:full )?(?:legal )?name\b|^name$", lambda: p.name),
            (r"e-?mail", lambda: p.email),
            (r"country code|dialing code|phone code", lambda: (p.phone or "").split()[0] if (p.phone or "").startswith("+") else "+91"),
            (r"phone|mobile|contact number|whatsapp", lambda: p.phone),
            (r"linked ?in", lambda: p.linkedin),
            (r"github", lambda: p.github),
            (r"portfolio|personal (?:web)?site|website|blog|other link", lambda: p.portfolio or p.github),
            (r"(?:current|present|most recent) (?:company|employer|organi[sz]ation)", lambda: p.current_company),
            (r"(?:current|present|most recent) (?:job )?(?:title|designation|role|position)", lambda: p.current_title),
            (r"notice period|how soon can you join|earliest (?:start|joining)|when can you (?:start|join)",
             lambda: self._notice(q)),
            (r"(?:current|present) (?:ctc|salary|compensation|annual|package|pay)", lambda: self._salary(job, True)),
            (r"expected (?:ctc|salary|compensation|annual|package|pay)|salary expectation|desired (?:salary|pay|compensation)"
             r"|compensation expectation", lambda: self._salary(job, False)),
            (r"years?.{0,40}(?:experience|exp)\b.{0,20}\b(?:with|in|using|on|of working with)\b\s+(?P<skill>.+)",
             None),
            (r"(?:total|overall|relevant|professional)?\s*(?:years of|yrs of|years)\s*(?:of )?(?:total |relevant |work |professional )?"
             r"experience|how many years", lambda: str(p.years)),
            (r"do you have (?:any )?(?:hands[- ]on |professional |prior )?experience (?:with|in|using) (?P<skill>.+)", None),
            (r"(?:authori[sz]ed|legally (?:allowed|permitted|eligible)|eligible|right) to work|work authori[sz]ation|work permit",
             lambda: YES if self._authorized(job) else NO),
            (r"sponsor", lambda: NO if self._authorized(job) else YES),
            (r"relocat", lambda: YES if p.willing_to_relocate else NO),
            (r"(?:work|working) (?:from|in|at) (?:the |our )?office|on-?site|hybrid|commut",
             lambda: YES if ({"onsite", "hybrid"} & {x.lower() for x in (p.open_to or [])}) else NO),
            (r"work(?:ing)? remote|remote(?:ly)? work|comfortable .*remote", lambda: YES if "remote" in [x.lower() for x in (p.open_to or [])] else NO),
            (r"(?:previously|ever|currently) (?:worked|been employed|employed) (?:at|by|with|for)|former employee|worked here before",
             lambda: YES if any(e and e in t for e in self.past_employers) or
             any(e and e in (job.company or "").lower() for e in self.past_employers) else NO),
            (r"(?:18|eighteen) years|legal age|at least 18", lambda: YES),
            (r"\bcity\b", lambda: p.city),
            (r"\bstate\b|province|region", lambda: p.state),
            (r"\bcountry\b", lambda: p.country),
            (r"current location|where are you (?:located|based)|^location", lambda: ", ".join(x for x in (p.city, p.country) if x)),
            (r"highest (?:degree|education|qualification)|^degree|education level|qualification", lambda: p.education),
            (r"graduat.*year|year of (?:graduation|passing)|passing year", lambda: str(p.graduation_year or "")),
            (r"\bgender\b|\bsex\b", lambda: (p.eeo or {}).get("gender")),
            (r"\brace\b|ethnic", lambda: (p.eeo or {}).get("ethnicity")),
            (r"veteran", lambda: (p.eeo or {}).get("veteran")),
            (r"disabilit", lambda: (p.eeo or {}).get("disability")),
            (r"pronoun", lambda: "Prefer not to say"),
            (r"hear about|how did you (?:find|learn|come across)|where did you (?:see|find)",
             lambda: self.source_label or p.how_did_you_hear),
            (r"cover letter|why (?:are you|do you want|would you like|you want|you are)|tell us (?:about|why)|"
             r"additional information|anything else|message to (?:the )?hiring|summary of your|introduce yourself",
             lambda: self.cover_text or None),
            (r"privacy|consent|i agree|agree to|acknowledge|terms|certify|declare|confirm that|accurate and complete",
             lambda: YES if q.kind in ("checkbox", "radio", "select", "combobox") else None),
        ]
        for pattern, handler in rules:
            m = re.search(pattern, t, re.I)
            if not m:
                continue
            if handler is None:   # skill questions
                skill = m.group("skill")
                if "do you have" in pattern:
                    keys = find_terms(skill)
                    if not keys:
                        return None
                    return YES if any(self.evidence_years.get(k, 0) > 0 for k in keys) else NO
                return self.skill_years(skill) or None
            value = handler()
            return None if value in (None, "", "None") else str(value)
        return None

    # -- public --------------------------------------------------------------
    def answer(self, q: Question, job: Job) -> str | None:
        label = q.clean_label
        if not label:
            return None
        cached = self.db.get_answer(label)
        if cached is not None:
            return cached
        value = self._rule_answer(q, job)
        if value is not None and q.options and q.kind in ("select", "radio", "combobox", "checkbox_group"):
            if choose_option(value, q.options) is None:
                value = None   # we know an answer but none of the offered options fits -> ask
        if value is not None:
            return value
        return self._ask(q, job)

    def _ask(self, q: Question, job: Job) -> str | None:
        label = q.clean_label
        if not self.interactive:
            self.db.add_pending(label, q.kind, q.options, job.id)
            return None
        print(f"\n? [{job.company} - {job.title}] {label}{' (required)' if q.required else ''}")
        if q.options:
            real = [o for o in q.options if o and not _PLACEHOLDER_OPTION.match(o.strip())]
            for i, o in enumerate(real, 1):
                print(f"   {i}. {o}")
        try:
            raw = input("  your answer (number/text, Enter = skip this field): ").strip()
        except EOFError:
            raw = ""
        if not raw:
            self.db.add_pending(label, q.kind, q.options, job.id)
            return None
        if q.options and raw.isdigit():
            real = [o for o in q.options if o and not _PLACEHOLDER_OPTION.match(o.strip())]
            if 1 <= int(raw) <= len(real):
                raw = real[int(raw) - 1]
        self.db.set_answer(label, raw, source="you")
        return raw


def pending_key(label: str) -> str:
    return question_key(label)
