"""Command line: python -m autopilot <command> [options]

  init       parse your resume, create profile.yaml, show what to fill in
  doctor     check config, email login, Claude key and browser
  discover   find + screen fresh jobs, show the ranked queue
  apply      tailor + apply to the queued jobs
  run        discover, then apply (the daily command)
  tailor     build tailored packages for queued jobs without applying
  answers    list / answer / edit the saved form answers
  status     what has happened so far
  report     write the HTML report and CSV export
  login      sign in to linkedin or naukri once, in the bot's browser
  mail-report  email the latest run's report + APPLY_MANUALLY files to yourself
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from autopilot.config import ROOT, load_env, load_profile, load_settings, save_profile


class Log:
    """print() that also appends to output/logs/<date>.log"""

    def __init__(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"{datetime.now():%Y-%m-%d}.log"

    def __call__(self, *parts) -> None:
        line = " ".join(str(p) for p in parts)
        print(line, flush=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%H:%M:%S} {line}\n")


def _context():
    load_env()
    settings = load_settings()
    from autopilot.db import Database

    return settings, load_profile(), Database(settings.db_path), Log(settings.output_dir / "logs")


def _options(args):
    from autopilot.pipeline import RunOptions

    return RunOptions(dry_run=args.dry_run, interactive=not args.unattended, review=args.review,
                      channels=[c.strip() for c in args.channels.split(",")] if args.channels else None,
                      max_applications=args.max)


def _check_profile(profile, log) -> bool:
    missing = profile.missing()
    if missing:
        log(f"! profile.yaml is missing: {', '.join(missing)} - run `python -m autopilot init` and fill them in.")
        return False
    return True


# --------------------------------------------------------------------------- #
def cmd_init(args) -> int:
    load_env()
    settings = load_settings()
    log = Log(settings.output_dir / "logs")
    from autopilot.apply.answers import skill_years_from_resume
    from autopilot.pipeline import get_master
    from autopilot.text import DISPLAY

    master = get_master(settings, log, force=args.reparse)
    profile_path = ROOT / "profile.yaml"
    if profile_path.exists():
        log("profile.yaml already exists - left untouched.")
    else:
        import yaml

        data = yaml.safe_load((ROOT / "profile.example.yaml").read_text(encoding="utf-8"))
        c = master.get("contact", {})
        name = master.get("name", "")
        loc = [p.strip() for p in re.split(r"[,\-]", c.get("location", "")) if p.strip()]
        role = (master.get("experience") or [{}])[0]
        edu = (master.get("education") or [{}])[0]
        years = re.findall(r"(?:19|20)\d{2}", edu.get("dates", ""))
        data.update({
            "name": name, "first_name": name.split()[0] if name else "",
            "last_name": " ".join(name.split()[1:]) if name else "", "email": c.get("email", ""),
            "phone": c.get("phone", ""), "city": loc[0] if loc else "", "state": loc[1] if len(loc) > 2 else "",
            "country": loc[-1] if loc else "India", "linkedin": c.get("linkedin", ""), "github": c.get("github", ""),
            "portfolio": c.get("portfolio", ""), "current_title": role.get("title", ""),
            "current_company": role.get("company", ""), "years_experience": settings.get("search.experience_years", 3),
            "education": f"{edu.get('text', '')}{' (' + years[-1] + ')' if years else ''}".strip(),
            "graduation_year": int(years[-1]) if years else None,
        })
        template = settings.root.parent / "bulk_mail_sender" / "template.html"
        if template.exists() and re.search(r"immediate\s+join", template.read_text(encoding="utf-8"), re.I):
            data.update(notice_period="Immediate", notice_period_days=0)
            log("notice_period set to 'Immediate' - that is what your current email template tells "
                "recruiters. Change it in profile.yaml if it is not right.")
        save_profile(data, profile_path)
        log(f"Created {profile_path} from your resume.")

    profile = load_profile()
    log("\nYears per skill, from which roles mention it (override any in profile.yaml -> skill_years):")
    evidence = skill_years_from_resume(master)
    top = sorted(evidence.items(), key=lambda kv: -kv[1])[:24]
    log("  " + ", ".join(f"{DISPLAY.get(k, k)} {v:g}y" for k, v in top))
    todo = profile.missing() + [k for k in ("notice_period", "current_ctc", "expected_ctc") if not profile.get(k)]
    log("\nNext steps:")
    log(f"  1. Review {settings.master_json} (the facts every tailored resume is built from).")
    if todo:
        log(f"  2. Fill these in {profile_path.name}: {', '.join(todo)}")
    log("  3. Copy .env.example to .env and add EMAIL_ADDRESS / EMAIL_PASSWORD (+ ANTHROPIC_API_KEY for Claude).")
    log("  4. python -m autopilot doctor        then   python -m autopilot run --dry-run")
    return 0


def cmd_doctor(args) -> int:
    settings, profile, db, log = _context()
    from autopilot.apply import existing_pipeline_addresses
    from autopilot.apply.email_channel import Mailer
    from autopilot.resume.parser import load_master, master_is_stale
    from autopilot.tailoring.llm import credentials_present

    ok = True
    good, bad = "  ok  ", "  !!  "
    log(f"{good if not profile.missing() else bad}profile.yaml" +
        (f" missing: {', '.join(profile.missing())}" if profile.missing() else ""))
    ok &= not profile.missing()
    for key in ("notice_period", "current_ctc", "expected_ctc"):
        if not profile.get(key):
            log(f"  ..  profile.{key} is empty - forms that ask will prompt you once")
    if settings.master_json.exists():
        stale = master_is_stale(load_master(settings.master_json), settings.resume_pdf)
        log(f"{bad if stale else good}master resume {settings.master_json.name}" +
            (" is older than the PDF (init --reparse)" if stale else ""))
    else:
        log(f"{bad}master resume not parsed yet - run init")
        ok = False
    mailer = Mailer(profile, dry_run=False, log=log)
    if mailer.problem():
        log(f"{bad}email: {mailer.problem()}")
    elif args.smtp:
        try:
            mailer._connect()
            mailer.close()
            log(f"{good}email: SMTP login works for {mailer.user}")
        except Exception as exc:  # noqa: BLE001
            log(f"{bad}email: {exc}")
            ok = False
    else:
        log(f"{good}email: credentials set for {mailer.user} (add --smtp to test the login)")
    if settings.get("tailoring.llm") == "anthropic":
        if not credentials_present():
            log("  ..  Claude: no ANTHROPIC_API_KEY - rule-based tailoring will be used")
        elif args.llm:
            try:
                import anthropic

                model = anthropic.Anthropic().models.retrieve(settings.get("tailoring.model"))
                log(f"{good}Claude: key works, model {model.id} available")
            except Exception as exc:  # noqa: BLE001
                log(f"{bad}Claude: {type(exc).__name__}: {exc}")
                ok = False
        else:
            log(f"{good}Claude: credentials found (add --llm to verify the key)")
    try:
        from autopilot import browser

        browser.headless_browser()
        browser.shutdown()
        log(f"{good}browser: headless Chromium starts")
    except Exception as exc:  # noqa: BLE001
        log(f"{bad}browser: {exc} -> run: .venv/bin/python -m playwright install chromium")
        ok = False
    chrome = Path("/Applications/Google Chrome.app").exists() or shutil.which("google-chrome")
    log(f"{good if chrome else '  ..  '}Google Chrome {'found' if chrome else 'not found - set browser.channel: chromium'}")
    log(f"{good}existing GitHub-Actions mail lists: {len(existing_pipeline_addresses(settings))} addresses will not be re-mailed")
    channels = settings.get("apply.channels", [])
    log(f"{good}channels enabled: {', '.join(channels)}")
    return 0 if ok else 1


def _print_queue(jobs, log, limit=40):
    from autopilot.text import display

    log(f"\n{'#':>3} {'T':>1} {'match':>5}  {'via':<10} {'job':<46} {'company':<24} location")
    for i, j in enumerate(jobs[:limit], 1):
        via = "email" if j.hr_email else j.apply_type.replace("linkedin_easy_apply", "easy-apply")[:10]
        log(f"{i:>3} {j.tier:>1} {j.score:>5.0%}  {via:<10} {j.title[:46]:<46} {j.company[:24]:<24} {j.location[:30]}")
    if len(jobs) > limit:
        log(f"... and {len(jobs) - limit} more")
    if jobs:
        log(f"\nTop job matches on: {', '.join(display(jobs[0].matched[:8]))}")


def cmd_discover(args) -> int:
    settings, profile, db, log = _context()
    if not _check_profile(profile, log):
        return 1
    from autopilot.pipeline import discover_and_screen, get_master

    result, _ = discover_and_screen(settings, profile, get_master(settings, log), db, log)
    jobs = result.accepted
    _print_queue(jobs, log)
    log(f"\n{len(jobs)} jobs queued. Next: python -m autopilot apply --dry-run")
    return 0


def _queued(db, args, settings=None):
    from autopilot.pipeline import job_from_row
    from autopilot.screening import freshness_reason

    jobs = []
    for row in db.jobs_by_status("queued"):
        job = job_from_row(row)
        stale = freshness_reason(job, settings) if settings is not None and job.date_posted else ""
        if stale:
            db.set_status(job.id, "rejected", f"went stale in the queue: {stale}")
            continue
        jobs.append(job)
    if getattr(args, "job", None):
        jobs = [j for j in jobs if j.id in args.job]
    return jobs


def _finish(settings, db, log, run_id, stats) -> None:
    from autopilot.reporting import write_report

    db.finish_run(run_id, dict(stats))
    path = write_report(db, settings.output_dir, db.run_started(run_id))
    log("\nDone: " + (", ".join(f"{k}={v}" for k, v in sorted(stats.items())) or "nothing to do"))
    log(f"Report: {path}")
    log(f"Log of every application: {settings.output_dir / 'applications_log.csv'}")


def cmd_apply(args, discover_first: bool = False) -> int:
    settings, profile, db, log = _context()
    if not _check_profile(profile, log):
        return 1
    from autopilot import browser
    from autopilot.pipeline import apply_jobs, discover_and_screen, get_master

    opts = _options(args)
    if args.headless:
        settings.data.setdefault("browser", {})["headless"] = True
    master = get_master(settings, log)
    run_id = db.start_run(("dry-run " if opts.dry_run else "") + ("run" if discover_first else "apply"))
    from collections import Counter

    from autopilot.runlog import RunFolder
    from autopilot.screening import screen

    run = RunFolder(settings.path("paths.runs", "../job_runs"))
    log(f"Run folder: {run.dir}")
    log("Search keywords (settings.yaml -> search.keywords): " + ", ".join(settings.get("search.keywords", [])))
    run.info["Search keywords (settings.yaml -> search.keywords)"] = ", ".join(settings.get("search.keywords", []))
    run.info["Mode"] = "dry run - nothing sent" if opts.dry_run else ("review" if opts.review else "live")
    sources, rejected = Counter(), []
    stats = Counter()
    try:
        if discover_first:
            result, sources = discover_and_screen(settings, profile, master, db, log)
            rejected = result.rejected
        # Re-check the whole queue with today's rules: jobs queued on earlier runs (or before
        # you edited settings.yaml) must pass the same screen as fresh ones.
        queue = screen(_queued(db, args, settings), settings, profile, master, db,
                       log=lambda *a: None, enrich_linkedin=False)
        jobs = queue.accepted
        if settings.get("apply.find_ats_forms.enabled", True):
            from datetime import timezone

            from autopilot.discovery.resolve import resolve_routes
            from autopilot.screening import hiring_chance, rank_key

            found = resolve_routes(jobs, settings, db, log)
            run.info["Employer forms found for link-only jobs"] = found
            if found:
                now = datetime.now(timezone.utc)
                for job in jobs:
                    job.chance = hiring_chance(job, settings, now)
                jobs.sort(key=lambda j: rank_key(j, now))
        run.accepted, run.rejected = jobs, rejected + queue.rejected
        log(f"\n{len(jobs)} queued jobs{' [DRY RUN - nothing is sent]' if opts.dry_run else ''}")
        stats = apply_jobs(jobs, settings, profile, master, db, opts, log, run=run)
    finally:
        browser.shutdown()
        summary = run.write(stats, sources)
        log(f"\nThis run's folder: {run.dir}\n  summary: {summary.name}  ·  workbook: {run.stamp}.xlsx")
        if run.manual:
            log(f"  apply by hand: {len(run.manual)} jobs in {run.manual_dir.name}/ (README.md lists them)")
        from autopilot.runlog import prune_old_runs

        prune_old_runs(run.dir.parent, int(settings.get("paths.keep_files_days", 30)), log)
    _finish(settings, db, log, run_id, stats)
    return 0


def cmd_tailor(args) -> int:
    settings, profile, db, log = _context()
    from autopilot import browser
    from autopilot.pipeline import get_master
    from autopilot.tailoring import Tailor

    jobs = _queued(db, args, settings)[: args.top]
    tailor = Tailor(get_master(settings, log), profile, settings, settings.output_dir, log)
    try:
        for job in jobs:
            pkg = tailor.build(job)
            db.set_status(job.id, "queued", "", str(pkg.folder))
            log(f"{job.title} @ {job.company}: {pkg.folder} [{pkg.method}, {pkg.pages} pages]")
            for note in pkg.notes:
                log(f"    guard: {note}")
    finally:
        browser.shutdown()
    return 0


def cmd_answers(args) -> int:
    settings, profile, db, log = _context()
    if args.set:
        question, _, answer = args.set.partition("=")
        db.set_answer(question.strip(), answer.strip())
        log(f"saved: {question.strip()} -> {answer.strip()}")
        return 0
    if args.delete:
        log("deleted" if db.delete_answer(args.delete) else "no such saved answer")
        return 0
    if args.pending:
        import json

        pending = db.pending()
        if not pending:
            log("No pending questions.")
        for p in pending:
            options = json.loads(p["options"] or "[]")
            print(f"\n? {p['question']}")
            for i, o in enumerate(options, 1):
                print(f"   {i}. {o}")
            raw = input("  answer (Enter = later): ").strip()
            if raw:
                if options and raw.isdigit() and 1 <= int(raw) <= len(options):
                    raw = options[int(raw) - 1]
                db.set_answer(p["question"], raw)
        return 0
    rows = db.all_answers()
    if not rows:
        log("No saved answers yet. They are added as forms ask questions your profile can't answer.")
    for r in rows:
        log(f"  {r['question'][:80]:<80} -> {r['answer']}  ({r['source']})")
    return 0


def cmd_status(args) -> int:
    settings, profile, db, log = _context()
    counts = db.status_counts()
    log("Jobs by status: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    log("Most common reasons jobs were skipped:")
    for reason, n in db.reject_reasons(10):
        log(f"  {n:>4}  {reason}")
    log("\nLatest applications:")
    for a in db.applications()[-15:]:
        log(f"  {a['created_at'][:16]}  {a['status']:<22} {a['channel']:<9} {(a['title'] or '')[:40]:<40} {a['company'] or ''}")
    pending = db.pending()
    if pending:
        log(f"\n{len(pending)} form questions wait for your answer: python -m autopilot answers --pending")
    return 0


def cmd_report(args) -> int:
    settings, profile, db, log = _context()
    from autopilot.reporting import export_csv, write_report

    since = args.since or "1970-01-01"
    log(f"Report: {write_report(db, settings.output_dir, since, title='Job Autopilot - all applications')}")
    log(f"CSV:    {export_csv(db, settings.output_dir / 'applications_export.csv')}")
    return 0


def cmd_login(args) -> int:
    settings, profile, db, log = _context()
    from autopilot import browser
    from autopilot.apply.portal import login

    try:
        ok = login(settings, args.site)
    finally:
        browser.shutdown()
    log(f"{args.site}: {'signed in - the session is saved in data/browser-profile' if ok else 'still on the login page'}")
    if ok:
        log(f"Enable it in settings.yaml -> apply.channels: [..., {args.site}]")
    return 0 if ok else 1


def cmd_mail_report(args) -> int:
    load_env()
    settings = load_settings()
    from autopilot.report_mail import latest_run, send

    run_dir = Path(args.run_dir) if args.run_dir else latest_run(settings.path("paths.runs", "../job_runs"))
    if run_dir is None or not run_dir.exists():
        print("No run folder to report.")
        return 1
    return 0 if send(run_dir) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m autopilot", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="parse the resume and create profile.yaml")
    p.add_argument("--reparse", action="store_true", help="re-read the PDF (replaces data/master_resume.json)")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("doctor", help="check the setup")
    p.add_argument("--smtp", action="store_true", help="also test the SMTP login")
    p.add_argument("--llm", action="store_true", help="also verify the Anthropic key")
    p.set_defaults(fn=cmd_doctor)

    sub.add_parser("discover", help="find and screen jobs").set_defaults(fn=cmd_discover)

    for name, help_text, first in (("apply", "apply to queued jobs", False), ("run", "discover, then apply", True)):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--dry-run", action="store_true", help="build everything, send nothing")
        p.add_argument("--review", action="store_true", help="fill forms but let you press Submit")
        p.add_argument("--unattended", action="store_true",
                       help="never wait for input (cron/CI); stuck jobs become needs_attention")
        p.add_argument("--channels", help="comma list overriding settings: email,ats,linkedin,naukri,manual")
        p.add_argument("--max", type=int, help="max applications this run")
        p.add_argument("--job", action="append", help="only this job id (repeatable)")
        p.add_argument("--headless", action="store_true", help="run the browser without a window (scheduled runs)")
        p.set_defaults(fn=lambda a, first=first: cmd_apply(a, discover_first=first))

    p = sub.add_parser("tailor", help="build packages for queued jobs without applying")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--job", action="append", help="only this job id (repeatable)")
    p.set_defaults(fn=cmd_tailor)

    p = sub.add_parser("answers", help="saved answers to form questions")
    p.add_argument("--pending", action="store_true", help="answer the questions forms asked")
    p.add_argument("--set", help='"Question text=answer"')
    p.add_argument("--delete", help="question text to forget")
    p.set_defaults(fn=cmd_answers)

    sub.add_parser("status", help="counts, recent applications").set_defaults(fn=cmd_status)

    p = sub.add_parser("report", help="HTML report + CSV export of all applications")
    p.add_argument("--since", help="ISO date, e.g. 2026-10-01")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("login", help="sign in once in the bot's browser")
    p.add_argument("site", choices=["linkedin", "naukri"])
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("mail-report", help="email the latest run's report to yourself")
    p.add_argument("--run-dir", help="a run folder (default: the newest in job_runs/)")
    p.set_defaults(fn=cmd_mail_report)

    args = parser.parse_args(argv)
    try:
        return int(args.fn(args) or 0)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
