"""discover -> screen -> tailor -> apply -> report."""

from __future__ import annotations

import json
import random
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from autopilot.apply import (ATTENTION, DRY_RUN, FAILED, JOB_STATUS, MANUAL, SUBMITTED, SUBMITTED_UNCONFIRMED,
                             ChannelDown, Outcome, choose_channel, existing_pipeline_addresses)
from autopilot.config import Profile, Settings
from autopilot.db import Database
from autopilot.models import Job
from autopilot.reporting import append_csv
from autopilot.resume.parser import build_master, load_master, master_is_stale, save_master


@dataclass
class RunOptions:
    dry_run: bool = False
    interactive: bool = True
    review: bool = False
    channels: list[str] | None = None
    max_applications: int | None = None
    job_ids: list[str] = field(default_factory=list)


def get_master(settings: Settings, log=print, force: bool = False) -> dict:
    path, pdf = settings.master_json, settings.resume_pdf
    if path.exists() and not force:
        master = load_master(path)
        if master_is_stale(master, pdf):
            log(f"! {pdf.name} changed since it was parsed. Re-parse with: python -m autopilot init --reparse "
                f"(your edits to {path.name} will be replaced)")
        return master
    if not pdf.exists():
        raise SystemExit(f"Resume PDF not found: {pdf} (set resume.pdf in settings.yaml)")
    master = build_master(pdf)
    save_master(master, path)
    log(f"Parsed {pdf.name} -> {path} (review it once; tailoring only reorders/rephrases it)")
    return master


def job_from_row(row) -> Job:
    from autopilot.discovery.common import to_datetime

    job = Job(source=row["source"], title=row["title"] or "", company=row["company"] or "", url=row["url"] or "",
              location=row["location"] or "", description=row["description"] or "",
              apply_url=row["apply_url"] or "", apply_type=row["apply_type"] or "external",
              hr_email=row["hr_email"] or "",
              # Undated posts came from age-filtered searches; their clock starts when first seen.
              date_posted=to_datetime(row["date_posted"]) or to_datetime(row["first_seen"]), date_trusted=True,
              is_remote=bool(row["is_remote"]), salary_min=row["salary_min"], salary_max=row["salary_max"],
              salary_currency=row["salary_currency"] or "", salary_interval=row["salary_interval"] or "",
              experience_text=row["experience_text"] or "", external_id=row["external_id"] or "",
              tier=row["tier"] or 0, score=row["score"] or 0.0, country=row["country"] or "")
    job.matched = json.loads(row["matched"] or "[]")
    job.missing = json.loads(row["missing"] or "[]")
    job.flags = json.loads(row["flags"] or "[]")
    return job


def discover_and_screen(settings: Settings, profile: Profile, master: dict, db: Database, log=print):
    """Returns (ScreenResult, postings found per source)."""
    from autopilot.discovery import discover
    from autopilot.screening import screen

    log("Discovering jobs...")
    jobs = discover(settings, log)
    sources = Counter(j.source.split(":")[0] if j.source.startswith("web:") else j.source for j in jobs)
    log(f"Found {len(jobs)} postings ({', '.join(f'{k} {v}' for k, v in sources.most_common())}). Screening...")
    result = screen(jobs, settings, profile, master, db, log)
    reasons = Counter(j.reject_reason.split(" (")[0] if not j.reject_reason.startswith("posted")
                      else "older than the freshness limit" for j in result.rejected)
    log(f"Kept {len(result.accepted)}, dropped {len(result.rejected)}. Top reasons: " +
        "; ".join(f"{r} x{n}" for r, n in reasons.most_common(6)))
    return result, sources


def _manual_reason(job: Job) -> str:
    from autopilot.models import APPLY_LINKEDIN, APPLY_LINKEDIN_OFFSITE, APPLY_NAUKRI

    return {APPLY_LINKEDIN: "LinkedIn Easy Apply - needs your LinkedIn login (or add 'linkedin' to apply.channels "
                            "on your Mac after `login linkedin`)",
            APPLY_LINKEDIN_OFFSITE: "LinkedIn 'apply on company site' - the employer link shows once you sign in "
                                    "to LinkedIn, and no Greenhouse/Lever/Ashby form was found for it",
            APPLY_NAUKRI: "Naukri apply - needs your Naukri login (or add 'naukri' to apply.channels on your Mac "
                          "after `login naukri`)"}.get(
        job.apply_type, "employer's own careers site (Workday / company portal - needs an account there)")


def _manual_note(job: Job) -> str:
    return f"{_manual_reason(job)}: {job.apply_url or job.url}"


def _why_by_hand(job: Job, outcome: Outcome) -> str:
    if outcome.status == MANUAL:
        return _manual_reason(job)
    if outcome.status == ATTENTION:
        return f"the bot filled the form but stopped - {outcome.detail}"
    return f"automatic attempt failed - {outcome.detail}"


def apply_jobs(jobs: list[Job], settings: Settings, profile: Profile, master: dict, db: Database,
               opts: RunOptions, log=print, run=None) -> Counter:
    from autopilot.apply.answers import AnswerBook
    from autopilot.apply.email_channel import Mailer
    from autopilot.apply.portal import PortalSession, apply_ats, apply_linkedin, apply_naukri
    from autopilot.tailoring import Tailor

    channels = list(opts.channels or settings.get("apply.channels", ["email", "ats"]))
    pipeline = (existing_pipeline_addresses(settings)
                if settings.get("apply.email.respect_existing_pipeline", True) else set())
    tailor = Tailor(master, profile, settings, run.dir if run else settings.output_dir, log, dated=run is None)
    mailer = Mailer(profile, opts.dry_run, log)
    if "email" in channels and mailer.problem():
        log(f"! email channel off: {mailer.problem()}")
        channels.remove("email")
    answers = AnswerBook(db, profile, master, interactive=opts.interactive, log=log)
    portal = PortalSession(settings, answers, opts.interactive,
                           auto_submit=bool(settings.get("apply.auto_submit", True)) and not opts.review, log=log)
    caps = {"email": settings.get("apply.email.max_per_run", 20), "ats": settings.get("apply.ats.max_per_run", 10),
            "linkedin": settings.get("apply.linkedin.max_per_run", 10),
            "naukri": settings.get("apply.naukri.max_per_run", 10), "manual": settings.get("apply.manual_max_per_run", 15)}
    total_cap = opts.max_applications or int(settings.get("apply.max_applications", 25))
    pause = settings.get("apply.pause_seconds", [20, 45])
    used, stats, down = Counter(), Counter(), set()
    log_csv = settings.output_dir / "applications_log.csv"

    # Automatic channels first (best-ranked first within them); hand-off packages after.
    first_route = {job.id: choose_channel(job, channels, db, settings, pipeline)[0] for job in jobs}
    jobs = sorted(jobs, key=lambda j: first_route[j.id] in ("manual", "skip"))
    handled: set[str] = set()

    for job in jobs:
        sent = stats[SUBMITTED] + stats[SUBMITTED_UNCONFIRMED]
        if sent >= total_cap:
            log(f"Reached the per-run cap of {total_cap} applications.")
            break
        channel, note = choose_channel(job, [c for c in channels if c not in down], db, settings, pipeline)
        if channel == "skip":
            db.set_status(job.id, "rejected", note)
            stats["skipped"] += 1
            if run is not None and job.hr_email:
                run.email_status[job.hr_email.lower()] = f"not sent: {note}"
            continue
        if used[channel] >= int(caps.get(channel, 10)):
            continue   # stays queued; the next run picks it up while it is still fresh
        used[channel] += 1
        handled.add(job.id)
        log(f"\n[{channel}] T{job.tier} {job.title} @ {job.company or '?'} ({job.location}) score {job.score:.0%}")
        try:
            # Claude tailoring is spent on applications the bot submits; hand-offs get the
            # rule-based version unless tailoring.llm_for_manual is on.
            package = tailor.build(job, use_llm=channel != "manual" or bool(settings.get("tailoring.llm_for_manual", False)))
        except Exception as exc:  # noqa: BLE001 - a broken package must not stop the run
            log(f"    could not build the application package: {type(exc).__name__}: {exc}")
            stats[FAILED] += 1
            if run is not None and channel == "manual":
                run.add_manual(job, _manual_reason(job))
            continue
        log(f"    package: {package.folder.name} ({package.method}{', ' + str(len(package.notes)) + ' guard notes' if package.notes else ''})")

        outcome: Outcome
        try:
            if channel == "email":
                outcome = mailer.send(job, package)
            elif channel == "manual":
                outcome = Outcome(MANUAL, _manual_note(job))
            elif opts.dry_run:
                outcome = Outcome(DRY_RUN, f"would apply via {channel}: {job.apply_url or job.url}")
            elif channel == "ats":
                outcome = apply_ats(portal, job, package)
            elif channel == "linkedin":
                outcome = apply_linkedin(portal, job, package)
            else:
                outcome = apply_naukri(portal, job, package)
        except ChannelDown as exc:
            log(f"    ! {channel} channel stopped for this run: {exc}")
            down.add(channel)
            continue
        except Exception as exc:  # noqa: BLE001 - record and keep going
            outcome = Outcome(FAILED, f"{type(exc).__name__}: {str(exc)[:200]}")

        log(f"    -> {outcome.status}: {outcome.detail}")
        if run is not None and outcome.status in (MANUAL, ATTENTION, FAILED):
            run.add_manual(job, _why_by_hand(job, outcome), package)   # files move to APPLY_MANUALLY/
        stats[outcome.status] += 1
        # A dry run leaves no lasting mark: every job stays queued for the real run.
        status = "queued" if opts.dry_run else JOB_STATUS.get(outcome.status, "queued")
        db.record_application(job.id, channel, outcome.status, outcome.recipient, str(package.resume_pdf), outcome.detail)
        if run is not None:
            run.record(job, channel, outcome, package)
        db.set_status(job.id, status, outcome.detail, str(package.folder))
        if channel == "email" and outcome.status == SUBMITTED:
            db.record_contact(job.hr_email, job.id)
        append_csv(log_csv, {"applied_at": datetime.now().isoformat(timespec="seconds"), "status": outcome.status,
                             "channel": channel, "tier": job.tier, "score": round(job.score, 3),
                             "company": job.company, "title": job.title, "location": job.location,
                             "source": job.source, "job_url": job.url, "apply_url": job.apply_url,
                             "recipient": outcome.recipient, "resume": str(package.resume_pdf),
                             "detail": outcome.detail})
        if outcome.status in (SUBMITTED, SUBMITTED_UNCONFIRMED) and not opts.dry_run:
            time.sleep(random.uniform(float(pause[0]), float(pause[1])))

    # Hand-off jobs left over the tailoring cap still get listed (link only), once: they are
    # marked manual so tomorrow's APPLY_MANUALLY folder holds only new posts.
    if run is not None:
        for job in jobs:
            if job.id not in handled and first_route[job.id] == "manual":
                run.add_manual(job, _manual_reason(job))
                if not opts.dry_run:
                    db.set_status(job.id, "manual", "listed in APPLY_MANUALLY (link only)")

    mailer.close()
    if tailor.llm is not None and tailor.llm.usage["calls"]:
        u = tailor.llm.usage
        log(f"LLM: {u['calls']} calls, {u['input']} input + {u['cache_read']} cached + {u['output']} output tokens"
            + (f" ({', '.join(f'{k} x{v}' for k, v in u['by_model'].items())})" if u.get("by_model") else ""))
    return stats
