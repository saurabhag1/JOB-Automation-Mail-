"""The truthfulness guard: every LLM-written sentence is checked against the
master resume before it can reach a recruiter.

Checks, per piece of text:
* technologies (from the vocabulary) must already be in the resume - for a
  bullet, in the *same role*, so nothing migrates between employers;
* numbers must already be in the source - for a bullet, the same bullet;
* acronyms / CamelCase names (CKA, SLOs, AZ-104, ...) must appear in the resume;
* no stated experience above the profile's years; no template placeholders.

Anything that fails is replaced by the original bullet or the deterministic
text, and the reason is written to the package's notes.
"""

from __future__ import annotations

import re

from autopilot.text import numbers_in, unsupported_terms

_YEARS_CLAIM = re.compile(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years|yrs)", re.I)
_PLACEHOLDER = re.compile(r"\[[^\]]{1,40}\]|\{[^}]{1,40}\}|<[^>]{1,40}>|lorem ipsum", re.I)
_TOKEN = re.compile(r"[A-Za-z0-9+#.]+")


def _caps_tokens(text: str) -> set[str]:
    """Tokens with 2+ capitals, e.g. EKS, DevOps, SLOs, AZ-104 -> {'AZ', '104'...}."""
    out = set()
    for tok in _TOKEN.findall(text or ""):
        tok = tok.strip(".")
        if sum(c.isupper() for c in tok) >= 2:
            out.add(tok)
    return out


_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")


def misattributed(candidate: str, units: list[str], years: int) -> list[str]:
    """Sentences whose metrics don't come from one resume bullet about the same thing.

    "cut deployment time by 50% while keeping 99.9% uptime" fails when 50% and 99.9%
    belong to different achievements, even though both numbers are in the resume.
    """
    from autopilot.text import find_terms, implied

    bad = []
    unit_info = [(numbers_in(u), implied(find_terms(u))) for u in units]
    for sentence in _SENTENCES.split(candidate or ""):
        nums = numbers_in(sentence) - {str(years), f"{years}+"}
        if not nums:
            continue
        terms = set(find_terms(sentence))
        ok = any(nums <= u_nums and (not terms or terms & u_terms) for u_nums, u_terms in unit_info)
        if not ok:
            bad.append(f"metrics {', '.join(sorted(nums))} not from one achievement")
    return bad


def problems(candidate: str, vocab_source: str, number_source: str, caps_source: str,
             years: int, units: list[str] | None = None) -> list[str]:
    found = []
    terms = unsupported_terms(candidate, vocab_source)
    if terms:
        found.append("adds " + ", ".join(terms))
    allowed_numbers = numbers_in(number_source) | {str(years), f"{years}+"}
    new_numbers = numbers_in(candidate) - allowed_numbers
    if new_numbers:
        found.append("new numbers " + ", ".join(sorted(new_numbers)))
    low_caps = caps_source.lower()
    unknown = sorted(t for t in _caps_tokens(candidate)
                     if not re.search(rf"(?<![a-z0-9]){re.escape(t.lower())}(?![a-z0-9])", low_caps))
    if unknown:
        found.append("unknown names " + ", ".join(unknown))
    for m in _YEARS_CLAIM.finditer(candidate):
        if float(m.group(1)) > years:
            found.append(f"claims {m.group(1)} years")
    if _PLACEHOLDER.search(candidate):
        found.append("placeholder text")
    if units:
        found += misattributed(candidate, units, years)
    return found


def resume_units(master: dict) -> list[str]:
    """Each separately-stated fact of the resume: bullets, summary, skill lines, certificates."""
    units = [master.get("summary", "")]
    for block in master.get("experience", []) + master.get("projects", []):
        units += [f"{b.get('label', '')}: {b['text']}" for b in block.get("bullets", [])]
    units += [f"{g.get('category', '')}: {', '.join(g.get('items', []))}" for g in master.get("skills", [])]
    for g in master.get("extras", []):
        units += g.get("bullets", [])
    return [u for u in units if u]


def check_bullets(llm_roles: list[dict], master: dict, resume_all: str, years: int):
    """Merge LLM bullets into the master's roles.

    Returns ({role_id: [bullet dicts in final order]}, notes). Every source
    bullet appears exactly once: rewritten if it passed, original otherwise.
    """
    from autopilot.resume.parser import _bullet

    notes: list[str] = []
    final: dict[str, list[dict]] = {}
    by_role = {r["id"]: r for r in master.get("experience", [])}
    llm_by_role = {r.get("role_id"): r.get("bullets", []) for r in llm_roles or []}
    for role_id, role in by_role.items():
        originals = {b["id"]: b for b in role["bullets"]}
        role_text = "\n".join(f"{b.get('label', '')}: {b['text']}" for b in role["bullets"])
        out, used = [], set()
        for item in llm_by_role.get(role_id, []):
            sid, text = item.get("source_id"), (item.get("text") or "").strip()
            if sid not in originals or sid in used or not text:
                continue
            used.add(sid)
            src = originals[sid]
            src_text = f"{src.get('label', '')}: {src['text']}"
            issues = problems(text, role_text, src_text, resume_all, years)
            if len(text) > 330:
                issues.append("too long")
            if issues:
                notes.append(f"kept original {sid}: " + "; ".join(issues))
                out.append(dict(src))
            else:
                rewritten = _bullet(text, sid)
                out.append(rewritten)
        for sid, src in originals.items():   # anything the LLM dropped keeps its place at the end
            if sid not in used:
                out.append(dict(src))
        final[role_id] = out
    return final, notes
