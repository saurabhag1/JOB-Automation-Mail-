"""Email a finished run's report to yourself.

The daily GitHub run cannot publish its folder (the repository is public), so
the folder comes to your inbox instead:

    Subject: Job Autopilot 09 Oct 11:00 AM IST - 14 HR emails sent, 2 forms, 18 to apply by hand
    Body:    the run summary (where every application went, what to apply to by hand)
    Files:   <run>.xlsx (all sheets), <run>.md, APPLY_MANUALLY.zip (tailored resumes to apply
             by hand), run.log

Sent to REPORT_EMAIL if set, else to EMAIL_ADDRESS (the account that sends).
"""

from __future__ import annotations

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


def build(run_dir: Path, sender: str, to: str) -> EmailMessage:
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


def send(run_dir: Path, log=print) -> bool:
    user, password = env("EMAIL_ADDRESS"), env("EMAIL_PASSWORD")
    to = env("REPORT_EMAIL") or user
    if not user or not password:
        log("Report email not sent: EMAIL_ADDRESS / EMAIL_PASSWORD are not set.")
        return False
    msg = build(run_dir, user, to)
    with smtplib.SMTP(env("SMTP_HOST", "smtp.gmail.com"), int(env("SMTP_PORT", "587")), timeout=60) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(user, password)
        smtp.send_message(msg)
    log(f"Report emailed to you: \"{msg['Subject']}\"")
    return True
