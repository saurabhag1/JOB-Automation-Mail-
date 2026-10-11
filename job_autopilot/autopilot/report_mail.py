"""Email a finished run's report to yourself.

The daily GitHub run cannot publish its folder (the repository is public), so
the folder comes to your inbox instead:

    Subject: Job Autopilot 09 Oct 11:00 AM IST - 14 HR emails sent, 2 forms, 18 to apply by hand
    Body:    the run summary (where every application went, what to apply to by hand)
    Files:   <run>.xlsx (all sheets), all_applications_so_far.xlsx (every application ever made),
             <run>.md, APPLY_MANUALLY.zip (tailored resumes to apply by hand), run.log

Sent to REPORT_EMAIL if set, else to profile.yaml's email, else to EMAIL_ADDRESS (the account
that sends). The body itself lists the emails sent, the forms submitted, every job to apply to by
hand (with links) and the form questions that need your answer once (profile.yaml -> answers).
"""

from __future__ import annotations

import csv
import html
import io
import re
import smtplib
import ssl
import zipfile
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

from autopilot.config import env
from autopilot.runlog import MANUAL_DIR

MAX_ATTACH = 20 * 1024 * 1024        # Gmail refuses messages over 25 MB (attachments grow ~33% when encoded)
_ITEM = re.compile(r"^- \*\*(.+?):\*\* (.*)$", re.M)


def latest_run(base: Path) -> Path | None:
    runs = [p for p in base.glob("20*_IST*") if p.is_dir()] if base.exists() else []
    return max(runs, key=lambda p: p.stat().st_mtime) if runs else None


def _zip_folder(folder: Path, skip_pdfs: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(folder.rglob("*")):
            if f.is_file() and not (skip_pdfs and f.suffix.lower() in (".pdf", ".png")):
                zf.write(f, f.relative_to(folder.parent))
    return buf.getvalue()


def _rows(path: Path) -> list[dict]:
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def _table(headers: list[str], rows: list[list[str]], links: set[int] = frozenset()) -> str:
    e = html.escape
    th = "".join(f'<th style="text-align:left;padding:6px 8px;border-bottom:2px solid #dadce0">{e(h)}</th>'
                 for h in headers)
    body = ""
    for r in rows:
        cells = []
        for i, v in enumerate(r):
            v = str(v or "")
            cell = f'<a href="{e(v)}" style="color:#1a73e8">open</a>' if i in links and v.startswith("http") else e(v)
            cells.append(f'<td style="padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top">{cell}</td>')
        body += f"<tr>{''.join(cells)}</tr>"
    return f'<table style="border-collapse:collapse;font-size:13px;width:100%"><tr>{th}</tr>{body}</table>'


def pending_yaml(pending: list[dict]) -> str:
    """profile.yaml lines to paste, one per unanswered form question."""
    lines = ["answers:"]
    for q in pending:
        opts = f"   # options: {' / '.join(q['options'][:6])}" if q.get("options") else ""
        lines.append(f'  "{q["question"].replace(chr(34), chr(39))}": ""{opts}')
    return "\n".join(lines)


def html_body(run_dir: Path, items: dict, pending: list[dict]) -> str:
    e = html.escape
    sent = [r for r in _rows(run_dir / "sent.csv") if r.get("Channel") != "manual"]
    emails = [r for r in sent if r.get("Channel") == "email"]
    forms = [r for r in sent if r.get("Channel") != "email"]
    manual = _rows(run_dir / "apply_manually.csv")
    stats = "".join(
        f'<td style="padding:8px 14px;border:1px solid #e0e0e0;text-align:center"><div style="font-size:20px;'
        f'font-weight:bold">{e(str(items.get(k, "0")))}</div><div style="font-size:12px;color:#5f6368">{e(label)}</div></td>'
        for k, label in (("Emails sent", "HR emails sent"), ("Forms submitted by the bot", "forms submitted"),
                         ("Jobs to apply by hand (APPLY_MANUALLY)", "to apply by hand"),
                         ("HR emails found (all, incl. skipped jobs)", "HR emails found"),
                         ("Jobs kept (genuine, this week, fit you)", "jobs kept")))
    parts = ['<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#202124;max-width:900px">',
             f'<h2 style="margin:0 0 8px">Job Autopilot - {e(items.get("Run started (IST)", run_dir.name))} IST</h2>',
             f'<table style="border-collapse:collapse;margin:0 0 16px"><tr>{stats}</tr></table>']
    if items.get("HR emails not sent, why") not in (None, "", "none"):
        parts.append(f'<p style="color:#5f6368;font-size:13px">HR emails not sent, why: '
                     f'{e(items["HR emails not sent, why"])}</p>')
    if pending:
        parts += ['<h3 style="margin:18px 0 6px;color:#b3261e">Information I need from you (once)</h3>',
                  '<p style="margin:0 0 6px;font-size:13px">These application-form questions had no answer in your '
                  'profile, so those forms were left for you. Fill the answers below into <b>profile.yaml</b> (and the '
                  'AUTOPILOT_PROFILE_YAML secret); every later run uses them automatically.</p>',
                  f'<pre style="background:#f6f8fa;border:1px solid #e0e0e0;padding:10px;font-size:12px;'
                  f'white-space:pre-wrap">{e(pending_yaml(pending))}</pre>']
    parts.append(f'<h3 style="margin:18px 0 6px">HR emails sent ({len(emails)})</h3>')
    parts.append(_table(["Time", "To", "Company", "Role", "Status", "Job post"],
                        [[r.get("Time (IST)"), r.get("Sent to / where"), r.get("Company"), r.get("Job title"),
                          r.get("Status"), r.get("Job post")] for r in emails], {5}) if emails else "<p>None this run.</p>")
    parts.append(f'<h3 style="margin:18px 0 6px">Application forms ({len(forms)})</h3>')
    parts.append(_table(["Time", "Company", "Role", "Result", "Form"],
                        [[r.get("Time (IST)"), r.get("Company"), r.get("Job title"), r.get("Status"),
                          r.get("Sent to / where")] for r in forms], {4}) if forms else "<p>None this run.</p>")
    parts.append(f'<h3 style="margin:18px 0 6px">Apply by hand ({len(manual)}) - tailored resumes in '
                 f'{MANUAL_DIR}.zip (attached)</h3>')
    parts.append(_table(["#", "Role", "Company", "Location", "Posted", "Why by hand", "Apply"],
                        [[r.get("#"), r.get("Job title"), r.get("Company"), r.get("Location"), r.get("Posted (IST)"),
                          r.get("Why by hand"), r.get("Apply here")] for r in manual], {6})
                 if manual else "<p>Nothing to apply by hand this run.</p>")
    parts.append('<p style="color:#5f6368;font-size:12px;margin-top:18px">Attached: the run workbook, '
                 'all_applications_so_far.xlsx, APPLY_MANUALLY.zip and run.log. Every HR email sent is also in '
                 'Gmail &rarr; Sent.</p></div>')
    return "".join(parts)


def build(run_dir: Path, sender: str, to: str, pending: list[dict] | None = None) -> EmailMessage:
    md_files = sorted(run_dir.glob("*.md"))
    summary = md_files[0].read_text(encoding="utf-8") if md_files else "The run wrote no summary - see run.log."
    items = dict(_ITEM.findall(summary))
    when = items.get("Run started (IST)", run_dir.name)
    subject = (f"Job Autopilot {when} IST - {items.get('Emails sent', '0')} HR emails sent, "
               f"{items.get('Forms submitted by the bot', '0')} forms, "
               f"{items.get('Jobs to apply by hand (APPLY_MANUALLY)', '0')} to apply by hand")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Date"] = sender, to, subject, formatdate(localtime=True)
    msg.set_content(summary[:200_000])
    msg.add_alternative(html_body(run_dir, items, pending or []), subtype="html")

    attachments: list[tuple[str, bytes, str, str]] = []
    for f in sorted(run_dir.glob("*.xlsx")) + md_files:
        sub = "vnd.openxmlformats-officedocument.spreadsheetml.sheet" if f.suffix == ".xlsx" else "markdown"
        attachments.append((f.name, f.read_bytes(), "application" if f.suffix == ".xlsx" else "text", sub))
    manual = run_dir / MANUAL_DIR
    if manual.exists():
        data = _zip_folder(manual)
        if len(data) > MAX_ATTACH // 2:
            data = _zip_folder(manual, skip_pdfs=True)   # still has every link, APPLY.md and email.txt
        attachments.append((f"{MANUAL_DIR}.zip", data, "application", "zip"))
    log = run_dir / "run.log"
    if log.exists():
        attachments.append(("run.log", log.read_bytes()[-3_000_000:], "text", "plain"))
    total = 0
    for name, data, main, sub in attachments:
        if total + len(data) > MAX_ATTACH:
            continue
        total += len(data)
        msg.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return msg


def send(run_dir: Path, log=print, profile_email: str = "", pending: list[dict] | None = None) -> bool:
    user, password = env("EMAIL_ADDRESS"), env("EMAIL_PASSWORD")
    to = env("REPORT_EMAIL") or profile_email or user
    if not user or not password:
        log("Report email not sent: EMAIL_ADDRESS / EMAIL_PASSWORD are not set.")
        return False
    msg = build(run_dir, user, to, pending)
    with smtplib.SMTP(env("SMTP_HOST", "smtp.gmail.com"), int(env("SMTP_PORT", "587")), timeout=60) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(user, password)
        smtp.send_message(msg)
    log(f"Report emailed to you ({to}): \"{msg['Subject']}\"")
    return True
