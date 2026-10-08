"""One folder per run, named with the Indian date and time it started:

    job_runs/2026-10-08_10-35-AM_IST/
        2026-10-08_10-35-AM_IST.xlsx   Summary · Jobs · HR Emails · Sent · Rejected (one sheet each)
        2026-10-08_10-35-AM_IST.md     the same, readable at a glance
        jobs.csv  hr_emails.csv  sent.csv  rejected.csv  apply_manually.csv
        APPLY_MANUALLY/                the jobs the bot could not submit itself, best first:
            README.md                  the list with links and why each needs you
            01-<company-role>/         tailored resume PDF, cover letter, message to paste, APPLY.md
        applications/<company-role>/   what the bot sent: resume PDF, cover letter, the exact email
"""

from __future__ import annotations

import csv
import re
import shutil
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autopilot.models import Job
from autopilot.text import classify_email, display

IST = timezone(timedelta(hours=5, minutes=30), "IST")
MANUAL_DIR = "APPLY_MANUALLY"
_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})_")


def ist_now() -> datetime:
    return datetime.now(IST)


def _ist(dt: datetime | None) -> str:
    return dt.astimezone(IST).strftime("%d %b %Y") if dt else ""


class RunFolder:
    def __init__(self, base: Path):
        self.started = ist_now()
        self.stamp = self.started.strftime("%Y-%m-%d_%I-%M-%p_IST")
        path, n = base / self.stamp, 2
        while path.exists():
            path, n = base / f"{self.stamp}-{n}", n + 1
        path.mkdir(parents=True)
        self.dir = path
        self.accepted: list[Job] = []
        self.rejected: list[Job] = []
        self.sent: list[dict] = []
        self.email_status: dict[str, str] = {}
        self.info: dict[str, object] = {}
        self.manual: list[dict] = []
        self.manual_dir = self.dir / MANUAL_DIR

    # ---------------------------------------------------------------- rows
    def _job_row(self, rank: int, j: Job) -> dict:
        age = (datetime.now(timezone.utc) - j.date_posted).days if j.date_posted else ""
        via = "email" if j.hr_email else j.apply_type.replace("linkedin_easy_apply", "LinkedIn Easy Apply")
        return {
            "Rank": rank, "Priority": {1: "1 Remote", 2: "2 India", 3: "3 Abroad"}.get(j.tier, j.tier),
            "Hiring chance %": round(j.chance * 100), "Skill match %": round(j.score * 100),
            "Job title": j.title, "Company": j.company, "Location": j.location, "Remote": "yes" if j.is_remote else "",
            "Posted (IST)": _ist(j.date_posted), "Days old": age, "Found on": j.source,
            "Applicants": j.applicants if j.applicants is not None else "",
            "Job post": j.url, "Apply at": j.apply_url or j.url, "Apply via": via,
            "HR email": j.hr_email, "Other emails": ", ".join(j.alt_emails),
            "Experience asked": j.experience_text, "Matched skills": ", ".join(display(j.matched[:12])),
            "Missing skills": ", ".join(display(j.missing[:8])), "Notes": "; ".join(j.flags),
        }

    def _hr_rows(self) -> list[dict]:
        """Every HR email found this run - kept jobs first - and what happened to it."""
        rows, seen = [], set()
        for j in self.accepted + [r for r in self.rejected if r.hr_email]:
            for email in [j.hr_email] + list(j.alt_emails):
                if not email or email.lower() in seen:
                    continue
                seen.add(email.lower())
                status = self.email_status.get(email.lower()) or (
                    f"not contacted: {j.reject_reason}" if j.reject_reason else "queued (over this run's cap)")
                rows.append({"HR email": email, "Type": classify_email(email), "Company": j.company,
                             "Job title": j.title, "Job post": j.url, "Found on": j.source,
                             "Posted (IST)": _ist(j.date_posted), "Status": status})
        return rows

    def record(self, job: Job, channel: str, outcome, package=None) -> None:
        to = outcome.recipient or (job.apply_url or job.url)
        self.sent.append({
            "Time (IST)": ist_now().strftime("%d %b %Y %I:%M %p"), "Channel": channel, "Sent to / where": to,
            "Company": job.company, "Job title": job.title, "Job post": job.url, "Status": outcome.status,
            "Subject": getattr(package, "subject", ""), "Resume file": str(getattr(package, "resume_pdf", "")),
            "Tailored by": getattr(package, "method", ""), "Detail": outcome.detail,
        })
        if outcome.recipient:
            self.email_status[outcome.recipient.lower()] = f"{outcome.status} {ist_now().strftime('%I:%M %p IST')}"

    def add_manual(self, job: Job, why: str, package=None) -> None:
        """List a job you have to apply to yourself. Its tailored files (if any)
        move to APPLY_MANUALLY/NN-<company-role>/, and *package* is updated to point there."""
        n = len(self.manual) + 1
        files = ""
        if package is not None and Path(package.folder).exists():
            dest = self.manual_dir / f"{n:02d}-{Path(package.folder).name}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(package.folder), str(dest))
            package.folder = dest
            package.resume_pdf = dest / Path(package.resume_pdf).name
            if getattr(package, "letter_pdf", None):
                package.letter_pdf = dest / Path(package.letter_pdf).name
            files = f"{MANUAL_DIR}/{dest.name}/"
            (dest / "APPLY.md").write_text(self._apply_note(job, why, package), encoding="utf-8")
        self.manual.append({
            "#": n, "Hiring chance %": round(job.chance * 100), "Job title": job.title, "Company": job.company,
            "Location": job.location, "Posted (IST)": _ist(job.date_posted), "Found on": job.source,
            "Apply here": job.apply_url or job.url, "Job post": job.url, "Why by hand": why,
            "Tailored files": files or "(link only - over this run's tailoring cap)",
            "Matched skills": ", ".join(display(job.matched[:10])),
        })

    @staticmethod
    def _apply_note(job: Job, why: str, package) -> str:
        resume = Path(package.resume_pdf).name
        letter = Path(package.letter_pdf).name if getattr(package, "letter_pdf", None) else ""
        lines = [f"# {job.title} - {job.company or 'company not stated'}", "",
                 f"- **Apply here:** {job.apply_url or job.url}", f"- **Job post:** {job.url}",
                 f"- **Location:** {job.location}", f"- **Posted:** {_ist(job.date_posted)} (found on {job.source})",
                 f"- **Why the bot did not submit it:** {why}", "",
                 "## What to use", "",
                 f"- Resume: `{resume}` (tailored to this posting)"]
        if letter:
            lines.append(f"- Cover letter: `{letter}`")
        lines += ["- Message / cover note to paste: `email.txt`", "",
                  "## Cover note", "", getattr(package, "cover_note", "") or "", ""]
        return "\n".join(lines)

    # --------------------------------------------------------------- write
    def write(self, stats: Counter, sources: Counter) -> Path:
        jobs = [self._job_row(i, j) for i, j in enumerate(self.accepted, 1)]
        hr = self._hr_rows()
        rejected = [{"Job title": j.title, "Company": j.company, "Location": j.location, "Found on": j.source,
                     "Job post": j.url, "HR email": j.hr_email, "Why skipped": j.reject_reason} for j in self.rejected]
        summary = [{"Item": k, "Value": v} for k, v in self._summary(stats, sources, len(hr), hr).items()]
        sheets = {"Summary": summary, "Apply manually": self.manual, "Jobs": jobs, "HR Emails": hr,
                  "Sent": self.sent, "Rejected": rejected}
        for name, rows in (("jobs", jobs), ("hr_emails", hr), ("sent", self.sent), ("rejected", rejected),
                           ("apply_manually", self.manual)):
            self._csv(self.dir / f"{name}.csv", rows)
        if self.manual:
            self.manual_dir.mkdir(exist_ok=True)
            (self.manual_dir / "README.md").write_text(self._manual_markdown(), encoding="utf-8")
        self._xlsx(self.dir / f"{self.stamp}.xlsx", sheets)
        md = self.dir / f"{self.stamp}.md"
        md.write_text(self._markdown(summary, jobs, hr), encoding="utf-8")
        return md

    @staticmethod
    def not_sent_reasons(hr: list[dict]) -> Counter:
        """Why found HR addresses got no email, e.g. {'already emailed by an earlier run': 6}."""
        out = Counter()
        for row in hr:
            status = row["Status"]
            if status.startswith(("submitted", "dry_run")):
                continue
            reason = re.sub(r"^(not sent|not contacted):\s*", "", status)
            reason = re.sub(r"\s*\d{1,2}:\d\d [AP]M IST$", "", reason)
            reason = re.sub(r"^emailed \d+ days? ago$", "already emailed by an earlier run (60-day gap)", reason)
            reason = re.sub(r"^asks for \d+\+ years.*", "asks for more years than you have", reason)
            reason = re.sub(r"\s*\(.*\)$", "", reason) if not reason.startswith("already emailed") else reason
            out[reason] += 1
        return out

    def _summary(self, stats: Counter, sources: Counter, hr_count: int, hr: list[dict] | None = None) -> dict:
        finished = ist_now()
        out = {
            "Run started (IST)": self.started.strftime("%d %b %Y %I:%M %p"),
            "Run finished (IST)": finished.strftime("%d %b %Y %I:%M %p"),
            "Postings found": sum(sources.values()),
            "Found per source": ", ".join(f"{k} {v}" for k, v in sources.most_common()),
            "Jobs kept (genuine, this week, fit you)": len(self.accepted),
            "Jobs skipped": len(self.rejected),
            "HR emails found (all, incl. skipped jobs)": hr_count,
            "Emails sent": sum(1 for r in self.sent if r["Channel"] == "email" and r["Status"] == "submitted"),
            "Forms submitted by the bot": sum(1 for r in self.sent if r["Channel"] != "email"
                                              and r["Status"] in ("submitted", "submitted_unconfirmed")),
            "Jobs to apply by hand (APPLY_MANUALLY)": len(self.manual),
            "HR emails not sent, why": "; ".join(f"{r} {n}" for r, n in self.not_sent_reasons(hr or []).most_common())
                                       or "none",
            "Applications by result": ", ".join(f"{k} {v}" for k, v in sorted(stats.items())) or "none",
        }
        out.update({k: v for k, v in self.info.items()})
        reasons = Counter(j.reject_reason.split(" (")[0] if not j.reject_reason.startswith("posted")
                          else "older than this week" for j in self.rejected)
        out["Top skip reasons"] = "; ".join(f"{r} {n}" for r, n in reasons.most_common(6))
        return out

    @staticmethod
    def _csv(path: Path, rows: list[dict]) -> None:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            if rows:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)

    @staticmethod
    def _xlsx(path: Path, sheets: dict[str, list[dict]]) -> None:
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font
        except ImportError:
            return
        wb = Workbook()
        wb.remove(wb.active)
        for name, rows in sheets.items():
            ws = wb.create_sheet(name)
            if not rows:
                ws.append(["(none)"])
                continue
            headers = list(rows[0])
            ws.append(headers)
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for row in rows:
                ws.append([row.get(h, "") for h in headers])
            for col, h in enumerate(headers, 1):
                width = max([len(str(h))] + [len(str(r.get(h, ""))) for r in rows[:200]])
                ws.column_dimensions[ws.cell(1, col).column_letter].width = min(60, max(10, width + 2))
            ws.freeze_panes = "A2"
        wb.save(path)

    def _markdown(self, summary: list[dict], jobs: list[dict], hr: list[dict]) -> str:
        lines = [f"# Job run {self.started.strftime('%d %b %Y, %I:%M %p')} IST", ""]
        lines += [f"- **{r['Item']}:** {r['Value']}" for r in summary]
        lines += ["", "## Where applications went", ""]
        if self.sent:
            lines += ["| Time (IST) | Channel | Sent to / where | Company | Job | Status |", "|---|---|---|---|---|---|"]
            lines += [f"| {s['Time (IST)']} | {s['Channel']} | {s['Sent to / where']} | {s['Company']} | "
                      f"{s['Job title']} | {s['Status']} |" for s in self.sent]
        else:
            lines.append("Nothing was sent this run.")
        lines += ["", f"## Apply by hand ({len(self.manual)}) - files in `{MANUAL_DIR}/`", ""]
        if self.manual:
            lines += self._manual_table()
        else:
            lines.append("Nothing to apply by hand this run.")
        lines += ["", f"## HR emails found ({len(hr)})", ""]
        if hr:
            lines += ["| HR email | Company | Job | Job post | Status |", "|---|---|---|---|---|"]
            lines += [f"| {h['HR email']} | {h['Company']} | {h['Job title']} | {h['Job post']} | {h['Status']} |"
                      for h in hr]
        lines += ["", f"## Jobs kept ({len(jobs)}), best first", "",
                  "| # | Priority | Chance | Match | Job | Company | Location | Posted | Apply via | Job post |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        lines += [f"| {j['Rank']} | {j['Priority']} | {j['Hiring chance %']}% | {j['Skill match %']}% | {j['Job title']} | "
                  f"{j['Company']} | {j['Location']} | {j['Posted (IST)']} | {j['Apply via']} | {j['Job post']} |"
                  for j in jobs]
        lines += ["", "Skipped jobs and the reason for each: `rejected.csv`. Tailored files: `applications/`."]
        return "\n".join(lines) + "\n"

    def _manual_table(self, inside: bool = False) -> list[str]:
        """Markdown rows; *inside* = for APPLY_MANUALLY/README.md (links relative to that folder)."""
        cell = lambda v: str(v).replace("|", "/").replace("\n", " ")  # noqa: E731
        out = ["| # | Chance | Job | Company | Location | Posted | Why by hand | Apply here | Files |",
               "|---|---|---|---|---|---|---|---|---|"]
        for m in self.manual:
            path = m["Tailored files"]
            if path.startswith(MANUAL_DIR):
                name = path.split("/")[1]
                files = f"[{name}]({path.split('/', 1)[1] if inside else path})"
            else:
                files = "link only"
            out.append("| " + " | ".join(cell(v) for v in (
                m["#"], f"{m['Hiring chance %']}%", m["Job title"], m["Company"], m["Location"], m["Posted (IST)"],
                m["Why by hand"], m["Apply here"])) + f" | {files} |")
        return out

    def _manual_markdown(self) -> str:
        lines = [f"# Apply by hand - run of {self.started.strftime('%d %b %Y, %I:%M %p')} IST", "",
                 "New jobs from this run that the bot could not submit itself, best chance first. Each folder has "
                 "the resume tailored to that job, a cover letter, the message to paste (`email.txt`) and "
                 "`APPLY.md` with the link.", ""]
        lines += self._manual_table(inside=True)
        return "\n".join(lines) + "\n"


def prune_old_runs(base: Path, keep_days: int, log=print) -> int:
    """Delete the bulky files (PDFs, .eml, screenshots) of run folders older than
    *keep_days*; the reports (.md/.xlsx/.csv) and email.txt are kept for good."""
    if keep_days <= 0 or not base.exists():
        return 0
    cutoff = (ist_now() - timedelta(days=keep_days)).date().isoformat()
    removed = 0
    for run in base.iterdir():
        m = _STAMP.match(run.name)
        if not run.is_dir() or not m or m.group(1) >= cutoff:
            continue
        for pattern in ("*.pdf", "*.eml", "*.png"):
            for f in run.rglob(pattern):
                f.unlink()
                removed += 1
    if removed:
        log(f"Tidied {removed} old PDF/email/screenshot files from run folders older than {keep_days} days.")
    return removed
