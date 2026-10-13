"""The email every recruiter receives: one fixed, clean layout, filled per job.

    Subject: Application for <Role> | <Name> | 3+ yrs DevOps & Cloud | Immediate Joiner

    Dear Hiring Team at <Company>,
    I'm applying for the <Role> role at <Company>. I'm a <descriptor> with 3+ years of hands-on
    experience in <top 3 skills this posting asks for>, and I can join immediately.

    Why I'm a good fit                      <- the 3 resume achievements that best match the posting
      • <Label>: <achievement, exact numbers>
    Skills that match this role             <- in both your resume and the posting
    Experience | Notice period | Location | Current role
    My resume is attached. ...
    <signature: name, title, phone, email, LinkedIn, GitHub, portfolio>

Nothing here is free text from an LLM: the achievements are the tailored resume's own
bullets (already checked by the truth guard), the skills are the overlap of your
resume and the posting, and everything else comes from profile.yaml.
"""

from __future__ import annotations

import html
import re

from autopilot.config import Profile
from autopilot.models import Job
from autopilot.text import DISPLAY, find_terms

_FONT = "font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.55;color:#202124"
_MUTED = "color:#5f6368"
_SOURCE_ID = re.compile(r"\s*\(?\b[ep]\d+\.b\d+\b\)?")


def clean(text: str) -> str:
    """No internal ids like (e1.b2), no doubled spaces."""
    return re.sub(r"\s{2,}", " ", _SOURCE_ID.sub("", text or "")).strip()


_SPECIAL = {"devops": "DevOps", "devsecops": "DevSecOps", "mlops": "MLOps", "sre": "SRE", "aws": "AWS",
            "gcp": "GCP", "ai": "AI", "it": "IT", "ci/cd": "CI/CD"}
_SMALL = {"and", "of", "at", "in", "for", "&", "the"}


def nice_title(text: str) -> str:
    """'Devops and cloud Engineer' -> 'DevOps and Cloud Engineer'."""
    words = (text or "").split()
    return " ".join(_SPECIAL.get(w.lower(), w if (i and w.lower() in _SMALL) or not w[:1].islower() else w.capitalize())
                     for i, w in enumerate(words))


def display_title(title: str) -> str:
    """'AZURE CLOUD ENGINEER' -> 'Azure Cloud Engineer'; anything already mixed-case stays as written."""
    letters = [c for c in title or "" if c.isalpha()]
    if letters and all(c.isupper() for c in letters) and len(letters) > 5:
        return nice_title(title.lower())
    return (title or "").strip()


def _notice_phrase(profile: Profile) -> tuple[str, str]:
    """('immediately', 'Immediate joiner') or ('with 30 days' notice', '30 days')."""
    notice = str(profile.notice_period or "").strip()
    if not notice or notice.lower().startswith("immediate") or str(profile.notice_period_days) == "0":
        return "immediately", "Immediate joiner"
    return f"with {notice} notice", notice


def highlights(resume: dict, weights: dict[str, float], n: int = 3) -> list[tuple[str, str]]:
    """[(label, achievement)] - the tailored resume's bullets that best match the posting,
    at most two from one role, most relevant first."""
    scored = []
    for r_idx, role in enumerate(resume.get("experience", [])):
        for b_idx, b in enumerate(role.get("bullets", [])):
            terms = find_terms(b.get("text", ""))
            score = sum(weights.get(t, 0.0) for t in terms)
            scored.append((-score, r_idx, b_idx, role.get("id"), b, terms))
    scored.sort(key=lambda s: s[:3])
    picked, per_role = [], {}
    for _, _, _, role_id, b, terms in scored:
        if per_role.get(role_id, 0) >= 2:
            continue
        per_role[role_id] = per_role.get(role_id, 0) + 1
        label = clean(b.get("label") or "")
        if not label:
            ranked = sorted(terms, key=lambda t: -weights.get(t, 0.0))
            label = " & ".join(DISPLAY.get(t, t) for t in ranked[:2]) or "Delivery"
        picked.append((label, clean(b.get("text", "")).rstrip(".") + "."))
        if len(picked) == n:
            break
    return picked


def subject(template: str, profile: Profile, job: Job) -> str:
    _, notice = _notice_phrase(profile)
    return re.sub(r"\s*\|\s*\|", " |", template.format(
        title=display_title(job.title) or "DevOps / Cloud Engineer", name=profile.name or "", years=profile.years,
        company=job.company or "", notice="Immediate Joiner" if notice == "Immediate joiner" else f"{notice} notice",
    )).strip(" |")


def build(job: Job, profile: Profile, resume: dict, skills: list[str], descriptor: str,
          company_known: bool, weights: dict[str, float], intro: str = "",
          extra_facts: tuple[tuple[str, str], ...] = ()) -> tuple[str, str]:
    """(plain text, html) for the email to the recruiter. *intro* may use {role} {at} {descriptor}
    {years} {skills} {join} (the freelance-leads project words it for contract work)."""
    p = profile
    role = display_title(job.title) or "DevOps / Cloud Engineer"
    company = job.company if company_known else ""
    at = f" at {company}" if company else ""
    join, notice = _notice_phrase(p)
    top = skills[:3]
    skill_part = (", ".join(top[:-1]) + " and " + top[-1]) if len(top) > 1 else (top[0] if top else "cloud infrastructure")
    greeting = f"Dear Hiring Team{at},"
    intro = (intro or "I'm applying for the {role} role{at}. I'm a {descriptor} with {years}+ years of hands-on "
             "experience in {skills}, and I can join {join}.").format(
        role=role, at=at, descriptor=descriptor, years=p.years, skills=skill_part, join=join)
    points = highlights(resume, weights)
    location = ", ".join(x for x in (p.city, p.country) if x)
    extras = []
    if p.willing_to_relocate:
        extras.append("open to relocation")
    if "remote" in [str(x).lower() for x in (p.open_to or [])]:
        extras.append("remote")
    facts = [("Experience", f"{p.years}+ years (DevOps & Cloud)"), ("Notice period", notice),
             ("Location", location + (f" ({', '.join(extras)})" if extras else ""))]
    title = nice_title(p.current_title or "")
    if title and p.current_company:
        facts.append(("Current role", f"{title} at {p.current_company}"))
    facts += list(extra_facts)
    closing = "My resume is attached. I'd welcome a short call at your convenience."
    links = [(k, v) for k, v in (("LinkedIn", p.linkedin), ("GitHub", p.github), ("Portfolio", p.portfolio)) if v]

    # ---- plain text
    lines = [greeting, "", intro, "", "Why I'm a good fit:"]
    lines += [f"  - {label}: {text}" for label, text in points]
    if skills:
        lines += ["", "Skills that match this role: " + ", ".join(skills[:10])]
    lines += [""] + [f"{k}: {v}" for k, v in facts] + ["", closing, "", "Regards,", p.name or ""]
    if title:
        lines.append(title)
    lines.append(" | ".join(x for x in (p.phone, p.email) if x))
    lines += [f"{k}: {v}" for k, v in links]
    plain = "\n".join(lines).strip() + "\n"

    # ---- html (inline styles only: Gmail strips <style>)
    e = html.escape
    li = "".join(f'<li style="margin:0 0 6px"><b>{e(label)}:</b> {e(text)}</li>' for label, text in points)
    chips = "".join(f'<span style="display:inline-block;margin:0 6px 6px 0;padding:2px 10px;border:1px solid #dadce0;'
                    f'border-radius:12px;font-size:12px;{_MUTED}">{e(s)}</span>' for s in skills[:10])
    rows = "".join(f'<tr><td style="padding:3px 16px 3px 0;{_MUTED};white-space:nowrap">{e(k)}</td>'
                   f'<td style="padding:3px 0">{e(v)}</td></tr>' for k, v in facts)
    link_html = " &nbsp;|&nbsp; ".join(f'<a href="{e(v)}" style="color:#1a73e8;text-decoration:none">{e(k)}</a>'
                                      for k, v in links)
    contact = " &nbsp;|&nbsp; ".join(
        x for x in ((f'<a href="tel:{e(re.sub(r"[^0-9+]", "", p.phone))}" style="color:#202124;text-decoration:none">'
                     f'{e(p.phone)}</a>') if p.phone else "",
                    (f'<a href="mailto:{e(p.email)}" style="color:#1a73e8;text-decoration:none">{e(p.email)}</a>')
                    if p.email else "") if x)
    body = f"""<div style="{_FONT};max-width:620px">
<p style="margin:0 0 12px">{e(greeting)}</p>
<p style="margin:0 0 14px">{e(intro).replace(e(role), f"<b>{e(role)}</b>", 1)}</p>
<p style="margin:0 0 6px;font-weight:bold">Why I'm a good fit</p>
<ul style="margin:0 0 14px 20px;padding:0">{li}</ul>
{f'<p style="margin:0 0 6px;font-weight:bold">Skills that match this role</p><div style="margin:0 0 12px">{chips}</div>' if chips else ''}
<table style="border-collapse:collapse;margin:0 0 14px;font-size:13px">{rows}</table>
<p style="margin:0 0 16px">{e(closing)}</p>
<div style="border-top:1px solid #e0e0e0;padding-top:10px">
<div style="font-weight:bold">{e(p.name or '')}</div>
{f'<div style="{_MUTED}">{e(title)}</div>' if title else ''}
<div>{contact}</div>
{f'<div>{link_html}</div>' if link_html else ''}
</div>
</div>"""
    return plain, body
