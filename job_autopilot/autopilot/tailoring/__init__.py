"""Build one application package per job: tailored resume PDF, cover letter
PDF, email body and a package.json audit trail (what changed and why)."""

from __future__ import annotations

import copy
import html as _html
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from autopilot.config import Profile, Settings
from autopilot.models import Job
from autopilot.resume.parser import resume_text
from autopilot.resume.render import render_letter_pdf, render_resume_pdf
from autopilot.tailoring import guard, rules
from autopilot.tailoring.free_llm import FreeLLMChain, free_keys_present
from autopilot.tailoring.llm import ClaudeTailor, LLMError, LLMUnavailable, credentials_present
from autopilot.text import find_terms, implied

_SIGN_OFF = re.compile(r"\n+\s*(?:best|kind|warm)?\s*(?:regards|sincerely|thanks|thank you)[^\n]*\s*(?:\n.*)?$",
                       re.I | re.S)


def slug(text: str, limit: int = 32) -> str:
    return (re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "x")[:limit]


def _paragraph_html(para: str) -> str:
    """A paragraph; its "- " lines become a real bullet list."""
    out, items = [], []
    for line in para.strip().split("\n"):
        if re.match(r"^\s*[-•*]\s+", line):
            items.append(f"<li>{_html.escape(re.sub(r'^\s*[-•*]\s+', '', line))}</li>")
            continue
        if items:
            out.append(f'<ul style="margin:4px 0 10px 18px;padding:0">{"".join(items)}</ul>')
            items = []
        out.append(f"<p style=\"margin:0 0 10px\">{_html.escape(line)}</p>")
    if items:
        out.append(f'<ul style="margin:4px 0 10px 18px;padding:0">{"".join(items)}</ul>')
    return "".join(out)


@dataclass
class Package:
    job_id: str
    folder: Path
    resume_pdf: Path
    letter_pdf: Path | None
    cover_note: str
    subject: str
    signature: str
    method: str
    pages: int = 0
    notes: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def email_text(self) -> str:
        return f"{self.cover_note}\n\n{self.signature}"

    @property
    def email_html(self) -> str:
        body = "".join(_paragraph_html(p) for p in self.cover_note.split("\n\n") if p.strip())
        sig = _html.escape(self.signature).replace("\n", "<br>")
        return f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5">{body}<p>{sig}</p></div>'


class Tailor:
    """Holds the per-run state: the parsed resume, and whether Claude is usable."""

    def __init__(self, master: dict, profile: Profile, settings: Settings, out_dir: Path, log=print,
                 dated: bool = True):
        self.master, self.profile, self.settings, self.log = master, profile, settings, log
        self.out_dir, self.dated = out_dir, dated
        self.resume_all = resume_text(master)
        self.master_keys = implied(find_terms(self.resume_all))
        self.units = guard.resume_units(master)
        template = settings.path("tailoring.email_template", "../bulk_mail_sender/template.html")
        self.template_text = ("\n\n".join(rules.template_paragraphs(template.read_text(encoding="utf-8")))
                              if template.exists() else "")
        self.llm: ClaudeTailor | FreeLLMChain | None = None
        mode = settings.get("tailoring.llm", "auto")
        if mode in ("anthropic", "auto") and credentials_present():
            self.llm = ClaudeTailor(settings.get("tailoring.model", "claude-opus-5-5"),
                                    settings.get("tailoring.effort", "medium"), master, profile)
        elif mode in ("free", "auto") and free_keys_present():
            self.llm = FreeLLMChain(settings, master, profile, self.template_text, log)
            log(f"Tailoring: free LLMs {', '.join(self.llm.names)} (best of {self.llm.best_of} drafts per job, "
                f"each checked by the truth guard)")
        elif mode != "none":
            log("Tailoring: no LLM key found - using rule-based tailoring (reorder + focus).")

    def signature(self) -> str:
        p = self.profile
        lines = ["Regards,", p.name or ""]
        contact = " | ".join(x for x in (p.phone, p.email) if x)
        if contact:
            lines.append(contact)
        if p.linkedin:
            lines.append(f"LinkedIn: {p.linkedin}")
        if p.portfolio:
            lines.append(f"Portfolio: {p.portfolio}")
        return "\n".join(lines)

    def _folder(self, job: Job) -> Path:
        name = f"{slug(job.company or 'company', 24)}-{slug(job.title, 28)}-{job.id[:6]}"
        base = self.out_dir / "applications"
        return base / date.today().isoformat() / name if self.dated else base / name

    def build(self, job: Job, use_llm: bool = True) -> Package:
        p, s = self.profile, self.settings
        weights = rules.jd_weights(job)
        resume = rules.tailor_resume(self.master, job, weights, int(s.get("tailoring.max_projects", 4)))
        summary = rules.summary(self.master, p, self.master_keys, weights)
        note = rules.cover_note(self.master, p, job, self.master_keys, weights)
        if self.template_text:
            from_template = rules.template_note(self.template_text, self.master, p, job, self.master_keys, weights)
            context = f"{self.resume_all}\n{job.title}\n{job.company}"
            if from_template and not guard.problems(from_template, self.resume_all, self.resume_all, context, p.years,
                                                    self.units):
                note = from_template    # your own email, adapted to this job
        subject = rules.subject(s, p, job)
        method, notes, missing = "rules", [], list(job.missing)

        if self.llm is not None and use_llm:
            try:
                if isinstance(self.llm, FreeLLMChain):
                    drafts = self.llm.drafts(job, rules._company_known(job))
                else:
                    drafts = [("claude", self.llm.tailor(job, rules._company_known(job)))]
            except LLMUnavailable as exc:
                self.log(f"Tailoring: LLM disabled for this run - {exc}")
                notes.append(f"LLM unavailable: {exc}")
                self.llm = None
            except LLMError as exc:
                notes.append(f"LLM failed for this job ({exc}); used rule-based tailoring")
            else:
                # Best of N: merge every draft through the guard, keep the one it had to fix least.
                scored = []
                for label, data in drafts:
                    trial = copy.deepcopy(resume)
                    merged = self._merge(job, data, trial, summary, note, subject)
                    scored.append((len(merged[3]), label, data, trial, merged))
                scored.sort(key=lambda s: s[0])
                issues, method, data, resume, (summary, note, subject, extra) = scored[0]
                # The best draft's cover note / summary may have failed the guard while another
                # draft's passed: use the passing one rather than the rule-based fallback.
                for part, flag in (("note", "kept rule-based cover note"), ("summary", "kept rule-based summary")):
                    if any(n.startswith(flag) for n in extra):
                        for other in scored[1:]:
                            if not any(n.startswith(flag) for n in other[4][3]):
                                if part == "note":
                                    note = other[4][1]
                                else:
                                    summary = other[4][0]
                                extra = [n for n in extra if not n.startswith(flag)] + [f"{part} from {other[1]}"]
                                break
                notes += extra
                if len(scored) > 1:
                    notes.append("drafts compared: " + ", ".join(f"{s[1]} ({s[0]} guard fixes)" for s in scored))
                missing = data.get("missing_requirements") or missing

        resume["summary"] = summary
        folder = self._folder(job)
        folder.mkdir(parents=True, exist_ok=True)
        attachment = s.get("resume.attachment_name", "{first}_{last}_Resume.pdf").format(
            first=p.first_name or "Resume", last=p.last_name or "", name=p.name or "")
        resume_pdf = folder / re.sub(r"_+", "_", attachment.replace(" ", "_"))
        highlight = set(job.matched) or {k for k in weights if k in self.master_keys}
        if s.get("tailoring.attach", "tailored") == "original" and s.resume_pdf.exists():
            shutil.copyfile(s.resume_pdf, resume_pdf)
            pages = 0
        else:
            pages = render_resume_pdf(resume, resume_pdf, highlight=highlight)

        package = Package(job_id=job.id, folder=folder, resume_pdf=resume_pdf, letter_pdf=folder / "Cover_Letter.pdf",
                          cover_note=note, subject=subject, signature=self.signature(), method=method,
                          pages=pages, notes=notes, missing=missing)
        contact = " | ".join(x for x in (p.email, p.phone, p.city) if x)
        render_letter_pdf(p.name or "", contact, package.email_text, package.letter_pdf)
        (folder / "email.txt").write_text(f"Subject: {subject}\n\n{package.email_text}\n", encoding="utf-8")
        meta = {
            "job": {"id": job.id, "title": job.title, "company": job.company, "location": job.location,
                    "url": job.url, "apply_url": job.apply_url, "apply_type": job.apply_type,
                    "hr_email": job.hr_email, "tier": job.tier, "score": round(job.score, 3),
                    "source": job.source},
            "method": method, "notes": notes, "missing_requirements": missing,
            "matched_terms": sorted(highlight), "pages": pages,
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "resume_order": {r["id"]: [b["id"] for b in r["bullets"]] for r in resume["experience"]},
        }
        (folder / "package.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        return package

    def _merge(self, job: Job, data: dict, resume: dict, summary: str, note: str, subject: str):
        years, notes = self.profile.years, []
        bullets, bullet_notes = guard.check_bullets(data.get("experience", []), self.master, self.resume_all, years)
        for role in resume["experience"]:
            role["bullets"] = bullets.get(role["id"], role["bullets"])
        notes += bullet_notes

        llm_summary = (data.get("summary") or "").strip()
        issues = guard.problems(llm_summary, self.resume_all, self.resume_all, self.resume_all, years, self.units)
        if not issues and 15 <= len(llm_summary.split()) <= 90:
            summary = llm_summary
        else:
            notes.append("kept rule-based summary: " + "; ".join(issues or ["length"]))

        llm_note = _SIGN_OFF.sub("", (data.get("cover_note") or "").strip()).strip()
        context = f"{self.resume_all}\n{job.title}\n{job.company}\n{job.location}\n{job.description}"
        issues = guard.problems(llm_note, self.resume_all, self.resume_all, context, years, self.units)
        if not issues and 50 <= len(llm_note.split()) <= 200:
            note = llm_note
        else:
            notes.append("kept rule-based cover note: " + "; ".join(issues or ["length"]))

        llm_subject = (data.get("email_subject") or "").strip()
        if 10 <= len(llm_subject) <= 120 and "\n" not in llm_subject and not guard.problems(
                llm_subject, self.resume_all, self.resume_all, context, years):
            subject = llm_subject
        return summary, note, subject, notes
