"""Deterministic tailoring - truthful by construction.

Nothing is rewritten: bullets, skills and projects are re-ordered so the ones
that match the posting come first, and the summary / cover note are assembled
only from terms and sentences that already exist in the master resume. This is
the whole tailoring when no LLM is configured, and the fallback for any part
of an LLM rewrite that fails the truthfulness guard.
"""

from __future__ import annotations

import copy
import re

from autopilot.config import Profile, Settings
from autopilot.models import Job
from autopilot.text import DISPLAY, WEIGHT, find_terms

_VERB_ED = re.compile(r"^[A-Za-z-]+ed\b")
_IRREGULAR = {"built", "led", "set", "ran", "wrote", "drove", "cut", "made", "took", "grew", "brought",
              "won", "spun", "began", "kept", "rebuilt", "upheld", "oversaw", "taught", "sped"}


def jd_weights(job: Job) -> dict[str, float]:
    """How much each vocabulary term matters to this posting."""
    terms = find_terms(f"{job.title}\n{job.description}")
    in_title = find_terms(job.title)
    return {k: WEIGHT.get(k, 1) * (1 + 0.25 * min(n - 1, 2)) * (1.5 if k in in_title else 1)
            for k, n in terms.items()}


def relevance(text: str, weights: dict[str, float]) -> float:
    return sum(weights.get(k, 0) for k in find_terms(text))


def _bullet_text(b: dict) -> str:
    return f"{b.get('label', '')}: {b['text']}" if b.get("label") else b["text"]


def descriptor(master: dict) -> str:
    """'DevOps & Cloud/SRE Engineer' - the candidate's own words for themselves."""
    summary = master.get("summary", "")
    head = re.split(r"\s+with\s+", summary, maxsplit=1)[0].strip()
    if head and len(head.split()) <= 8 and head != summary:
        return head
    roles = [r.get("title", "") for r in master.get("experience", []) if r.get("title")]
    return roles[0] if roles else "DevOps Engineer"


def tailor_resume(master: dict, job: Job, weights: dict[str, float], max_projects: int) -> dict:
    """A copy of the master with the most relevant content first."""
    resume = copy.deepcopy(master)
    for role in resume.get("experience", []):
        role["bullets"].sort(key=lambda b: -relevance(_bullet_text(b), weights))
    for group in resume.get("skills", []):
        group["items"].sort(key=lambda item: -relevance(item, weights))
    resume.get("skills", []).sort(key=lambda g: -sum(relevance(i, weights) for i in g.get("items", [])))
    projects = resume.get("projects", [])
    projects.sort(key=lambda p: -relevance(" ".join([p["name"], p.get("tech", "")]
                                                   + [_bullet_text(b) for b in p["bullets"]]), weights))
    resume["projects"] = projects[:max_projects]
    return resume


def matched_display(master_keys: set[str], weights: dict[str, float], limit: int) -> list[str]:
    keys = sorted((k for k in weights if k in master_keys), key=lambda k: -weights[k])
    # Skip vague terms like "Automation"/"Monitoring" when there are concrete ones.
    concrete = [k for k in keys if WEIGHT.get(k, 1) >= 1] or keys
    return [DISPLAY[k] for k in concrete[:limit]]


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def summary(master: dict, profile: Profile, master_keys: set[str], weights: dict[str, float]) -> str:
    terms = matched_display(master_keys, weights, 10)
    lead, rest = terms[:6], terms[6:10]
    years = profile.years
    text = f"{descriptor(master)} with {years}+ years of hands-on experience"
    if lead:
        text += f" in {_join(lead)}."
    else:
        text += " building and running cloud infrastructure."
    if rest:
        text += f" Also skilled in {_join(rest)}."
    # Keep the master summary's certification sentence, word for word.
    for sentence in re.split(r"(?<=\.)\s+", master.get("summary", "")):
        if "certified" in sentence.lower():
            text += " " + sentence.strip()
            break
    return text


def _as_first_person(bullet: dict) -> str | None:
    """'Built pipelines ...' -> 'built pipelines ...' (for 'At X, I built ...')."""
    text = bullet["text"].strip().rstrip(".")
    first = text.split()[0] if text else ""
    if _VERB_ED.match(first) or first.lower() in _IRREGULAR:
        return first.lower() + text[len(first):]
    return None


def _company_known(job: Job) -> bool:
    # Email-lead sources only guess the company from the address; don't name it.
    return bool(job.company) and job.source not in ("hr-leads", "pasted")


def cover_note(master: dict, profile: Profile, job: Job, master_keys: set[str],
               weights: dict[str, float]) -> str:
    company = job.company if _company_known(job) else ""
    greeting = f"Dear Hiring Team{' at ' + company if company else ''},"
    title = job.title or "DevOps / Cloud Engineer"
    terms = matched_display(master_keys, weights, 4)
    years = profile.years
    first = (f"I'm writing to apply for the {title} role{' at ' + company if company else ''}. "
             f"I'm a {descriptor(master)} with {years}+ years of hands-on experience")
    first += f", and my work lines up closely with what you need, especially {_join(terms)}." if terms else "."

    lines = []
    for idx, role in enumerate(master.get("experience", [])[:2]):
        ranked = sorted(role.get("bullets", []), key=lambda b: -relevance(_bullet_text(b), weights))
        for bullet in ranked:
            phrase = _as_first_person(bullet)
            if phrase:
                lead = "At" if idx == 0 else "Before that, at"
                lines.append(f"{lead} {role.get('company') or 'my previous company'}, I {phrase}.")
                break
    notice = (profile.notice_period or "").strip()
    if notice.lower().startswith("immediate"):
        availability = "I can join immediately."
    elif notice:
        availability = f"My notice period is {notice}."
    else:
        availability = ""
    closing = (f"{availability} I've attached my resume tailored to this role and would welcome "
               f"a short conversation about how I can help your team.").strip()
    return "\n\n".join(p for p in [greeting, first, " ".join(lines), closing] if p)


def subject(settings: Settings, profile: Profile, job: Job) -> str:
    template = settings.get("apply.email.subject", "Application for {title} | {name}")
    return template.format(title=job.title or "DevOps / Cloud Engineer", name=profile.name or "",
                           years=profile.years, company=job.company or "").strip(" |")


# --------------------------------------------------------------------------- #
# Your own email template (bulk_mail_sender/template.html), adapted per job
# --------------------------------------------------------------------------- #
_SPELLING = [(re.compile(r"\bI'?m a Immediate joinn?er\b", re.I), "I'm an immediate joiner"),
             (re.compile(r"\bjoinner\b", re.I), "joiner")]


def template_paragraphs(html_text: str) -> list[str]:
    """Paragraph texts of the template, without the sign-off block."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text or "", "html.parser")
    paras = [re.sub(r"\s+", " ", p.get_text(" ")).strip() for p in soup.find_all("p")] or \
        [re.sub(r"\s+", " ", soup.get_text(" ")).strip()]
    paras = [re.sub(r"\s+([.,])", r"\1", p) for p in paras if p]
    out = []
    for p in paras:
        if re.match(r"(?i)^(regards|best|thanks|thank you|sincerely)\b", p):
            break
        for rx, fixed in _SPELLING:
            p = rx.sub(fixed, p)
        out.append(p)
    return out


def template_note(template_text: str, master: dict, profile: Profile, job: Job, master_keys: set[str],
                  weights: dict[str, float]) -> str:
    """Your usual email with the role, company and a job-specific line filled in."""
    paras = [p for p in template_text.split("\n\n") if p.strip()]
    if len(paras) < 2:
        return ""
    company = job.company if _company_known(job) else ""
    title = job.title or "DevOps / Cloud Engineer"
    out = []
    for p in paras:
        if re.match(r"(?i)^dear\b", p):
            p = f"Dear Hiring Team{' at ' + company if company else ''},"
        p = re.sub(r"(?i)\bthe\s+[^.]{0,60}?\brole\b", f"the {title} role{' at ' + company if company else ''}", p, count=1)
        out.append(p)
    terms = matched_display(master_keys, weights, 4)
    best = None
    for role in master.get("experience", [])[:1]:
        for bullet in sorted(role.get("bullets", []), key=lambda b: -relevance(_bullet_text(b), weights)):
            best = _as_first_person(bullet)
            if best:
                break
    if terms and best:
        line = (f"What matches this role most closely is my work with {_join(terms)} - for example, at "
                f"{master['experience'][0].get('company') or 'my current company'} I {best}.")
        out.insert(min(2, len(out) - 1), line)
    return "\n\n".join(out)
