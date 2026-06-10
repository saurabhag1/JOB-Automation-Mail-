#!/usr/bin/env python3
"""Send your resume + template email to the genuine HR emails collected by
``collector.py``.

Reads ``daily_job_leads.json`` and emails each lead's primary address, attaching
your resume and a personalized HTML body. Reuses the template + resume that the
existing ``bulk_mail_sender`` workflow uses (paths are configurable).

Designed to run right after ``collector.py`` (locally or in GitHub Actions).

    python3 send_resume.py --dry-run            # preview, send nothing
    python3 send_resume.py --max-send 10        # actually send (needs creds)

Credentials come from the environment (or .env):
    EMAIL_ADDRESS   your Gmail address
    EMAIL_PASSWORD  a Gmail App Password (not your normal password)
"""

import argparse
import json
import os
import smtplib
import sys
import time
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

# Default to the assets already in the repo's bulk_mail_sender folder.
_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TEMPLATE = os.path.join(_HERE, "..", "bulk_mail_sender", "template.html")
DEFAULT_RESUME = os.path.join(_HERE, "..", "bulk_mail_sender", "Saurabh_Agrawal_2026.pdf")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Email resume to collected job leads.")
    p.add_argument("--leads", default="daily_job_leads.json", help="Leads JSON from collector.py.")
    p.add_argument("--template", default=DEFAULT_TEMPLATE, help="HTML email body template.")
    p.add_argument("--resume", default=DEFAULT_RESUME, help="Resume PDF to attach.")
    p.add_argument("--subject", default="Application for DevOps / Cloud Engineer Role",
                   help="Email subject line.")
    p.add_argument("--max-send", type=int, default=10, help="Max emails to send this run.")
    p.add_argument("--delay", type=float, default=8.0, help="Seconds to wait between sends.")
    p.add_argument("--dry-run", action="store_true", help="Log recipients but send nothing.")
    return p.parse_args(argv)


def load_leads(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("leads", data.get("jobs", []))


def build_message(sender, lead, subject, html_template, resume_path):
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = lead["email"]
    msg["Subject"] = subject

    body = (html_template
            .replace("{{company}}", lead.get("company", ""))
            .replace("{{position}}", lead.get("position", ""))
            .replace("{{location}}", lead.get("location", "")))
    msg.attach(MIMEText(body, "html"))

    with open(resume_path, "rb") as fh:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(fh.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition",
                    f"attachment; filename={os.path.basename(resume_path)}")
    msg.attach(part)
    return msg


def main(argv=None):
    args = parse_args(argv)

    if not os.path.exists(args.leads):
        print(f"No leads file at {args.leads}; nothing to send.")
        return 0
    leads = load_leads(args.leads)[: args.max_send]
    if not leads:
        print("No leads to send.")
        return 0

    with open(args.template, "r", encoding="utf-8") as fh:
        html_template = fh.read()

    sender = os.getenv("EMAIL_ADDRESS")
    password = os.getenv("EMAIL_PASSWORD")

    print(f"=== Sending resume to {len(leads)} lead(s){' [DRY RUN]' if args.dry_run else ''} ===")

    if args.dry_run:
        for i, lead in enumerate(leads, 1):
            print(f"  {i:>2}. WOULD email {lead['email']:<38} ({lead.get('company','')} / "
                  f"{lead.get('position','')})")
        print("Dry run complete — no emails sent.")
        return 0

    if not sender or not password:
        print("ERROR: EMAIL_ADDRESS / EMAIL_PASSWORD not set. Aborting send.", file=sys.stderr)
        return 1
    if not os.path.exists(args.resume):
        print(f"ERROR: resume not found at {args.resume}. Aborting.", file=sys.stderr)
        return 1

    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()
    server.login(sender, password)
    print("Logged into Gmail.")

    sent, failed = 0, 0
    for i, lead in enumerate(leads, 1):
        try:
            msg = build_message(sender, lead, args.subject, html_template, args.resume)
            server.sendmail(sender, lead["email"], msg.as_string())
            sent += 1
            print(f"  {i:>2}. sent -> {lead['email']}")
        except Exception as exc:  # noqa: BLE001 - keep going on individual failures
            failed += 1
            print(f"  {i:>2}. FAILED {lead['email']}: {exc}")
        if i < len(leads):
            time.sleep(args.delay)

    server.quit()
    print(f"\nDone. Sent {sent}, failed {failed}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
