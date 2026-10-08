"""Applying: pick the channel for each job and run it.

Channels, in order of preference:
  email    - an HR address was published for this job      (SMTP, fully automatic)
  ats      - Greenhouse / Lever / Ashby hosted form          (browser, fills + submits)
  linkedin - LinkedIn Easy Apply                             (browser, your logged-in session)
  naukri   - Naukri apply + recruiter questions              (browser, your logged-in session)
  manual   - anything else: you get the tailored resume and a clean link
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from autopilot.config import REPO, Settings
from autopilot.db import Database
from autopilot.models import APPLY_LINKEDIN, APPLY_NAUKRI, ATS_TYPES, Job

# Outcome statuses
SUBMITTED = "submitted"
SUBMITTED_UNCONFIRMED = "submitted_unconfirmed"
ALREADY = "already_applied"
DRY_RUN = "dry_run"
MANUAL = "manual"
ATTENTION = "needs_attention"
FAILED = "failed"
SKIPPED = "skipped"

JOB_STATUS = {SUBMITTED: "applied", SUBMITTED_UNCONFIRMED: "applied", ALREADY: "applied", MANUAL: "manual",
              ATTENTION: "needs_attention", FAILED: "failed", DRY_RUN: "queued", SKIPPED: "queued"}


@dataclass
class Outcome:
    status: str
    detail: str = ""
    recipient: str = ""
    extra: dict = field(default_factory=dict)


class ChannelDown(Exception):
    """The whole channel is unusable this run (bad login, daily limit hit)."""


def existing_pipeline_addresses(settings: Settings) -> set[str]:
    """Addresses the repo's GitHub Actions mailers already contacted.

    * bulk_mail_sender/mail_archive_7days/*.json - what the bulk sender mailed
    * bulk_mail_sender/Pasted text(1).txt        - today's bulk list
    * job_lead_analyzer/state.json               - what the lead collector mailed
    A list whose source is switched to "takeover" here is not counted, because
    then the autopilot is the one mailing it.
    """
    seen: set[str] = set()

    def add_job_file(path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for job in data.get("jobs", []):
            for email in str(job.get("emails", "")).split(";"):
                if email.strip():
                    seen.add(email.strip().lower())

    for path in (REPO / "bulk_mail_sender" / "mail_archive_7days").glob("jobs_*.json"):
        add_job_file(path)
    if not settings.get("sources.pasted_jobs.enabled", False):
        add_job_file(REPO / "bulk_mail_sender" / "Pasted text(1).txt")
    if not settings.get("sources.hr_email_leads.enabled", False):
        try:
            state = json.loads((REPO / "job_lead_analyzer" / "state.json").read_text(encoding="utf-8"))
            seen.update(e.lower() for e in state.get("seen_emails", []))
        except (OSError, ValueError):
            pass
    return seen


def email_block_reason(job: Job, db: Database, settings: Settings, pipeline: set[str]) -> str:
    address = job.hr_email.lower()
    if address in pipeline:
        return "already mailed by your GitHub Actions pipeline"
    last = db.last_contacted(address)
    window = int(settings.get("apply.email.recontact_after_days", 60))
    if last and datetime.now(timezone.utc) - last < timedelta(days=window):
        return f"emailed {(datetime.now(timezone.utc) - last).days} days ago"
    return ""


def choose_channel(job: Job, channels: list[str], db: Database, settings: Settings,
                   pipeline: set[str]) -> tuple[str, str]:
    """(channel, note). Channel is one of email/ats/linkedin/naukri/manual/skip."""
    email_note = ""
    if job.hr_email and "email" in channels:
        email_note = email_block_reason(job, db, settings, pipeline)
        if not email_note:
            return "email", ""
    if job.apply_type in ATS_TYPES and "ats" in channels:
        return "ats", ""
    if job.apply_type == APPLY_LINKEDIN and "linkedin" in channels:
        return "linkedin", ""
    if job.apply_type == APPLY_NAUKRI and "naukri" in channels:
        return "naukri", ""
    if email_note:
        return "skip", email_note        # the recruiter already has your resume
    return "manual", ""
