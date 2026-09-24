#!/usr/bin/env python3
"""7-day follow-up mailer for the bulk job-application flow.

Every run does two things:

1. **Archive** - today's original job file (the same ``Pasted text(1).txt`` that
   ``extract.py`` / ``send_mail.py`` just used) is copied into
   ``mail_archive_7days/jobs_<YYYY-MM-DD>.json``.

2. **Follow up** - the *oldest* archived file that is at least 7 **days** old
   is opened, its HR addresses are re-derived, everyone who replied or whose
   mail bounced is dropped, and whoever is left gets
   ``followup_template.html`` + the resume. That file is then deleted, so the
   next-oldest cohort takes its turn tomorrow.

The wait is measured in days, not in how many files the folder happens to
hold. The pipeline only runs Mon-Fri and skips any day without a fresh job
list, so a file count never reaches 7 on schedule - gating on the count meant
follow-ups never went out at all.

    python3 followup.py --status      # just print the folder
    python3 followup.py --dry-run     # show the plan, send nothing
    python3 followup.py               # archive + send

Credentials come from the environment / .env:
    EMAIL_ADDRESS   your Gmail address
    EMAIL_PASSWORD  a Gmail App Password

The "not opened" caveat: SMTP/IMAP cannot report opens. What this script can
see is *delivered and unanswered* - it drops bounced addresses (so only mail
that really landed in a mailbox is followed up) and drops anyone who replied.
If you ever host an open-tracking pixel, point TRACKING_OPENS_URL at its JSON
and genuine opens get dropped too.
"""

import argparse
import hashlib
import imaplib
import json
import os
import re
import shutil
import smtplib
import sys
import time
from datetime import date, datetime
from email import encoders, message_from_bytes
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr

from dotenv import load_dotenv

load_dotenv()

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ARCHIVE = os.path.join(HERE, "mail_archive_7days")
DEFAULT_SOURCE = os.path.join(HERE, "Pasted text(1).txt")
DEFAULT_TEMPLATE = os.path.join(HERE, "followup_template.html")
DEFAULT_RESUME = os.path.join(HERE, "Saurabh_Agrawal_2026.pdf")

WINDOW = 7                      # days a cohort waits before it is followed up
STALE_AFTER = 21                # extra days before an un-sent cohort is abandoned
ARCHIVE_NAME = "jobs_{}.json"
ARCHIVE_RE = re.compile(r"^jobs_(\d{4}-\d{2}-\d{2})\.json$")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
BOUNCE_SENDERS = ("mailer-daemon", "postmaster")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Send the 7-day follow-up mail.")
    p.add_argument("--source", default=DEFAULT_SOURCE, help="Today's original job file.")
    p.add_argument("--archive-dir", default=DEFAULT_ARCHIVE, help="Rolling 7-day folder.")
    p.add_argument("--template", default=DEFAULT_TEMPLATE, help="Follow-up HTML body.")
    p.add_argument("--resume", default=DEFAULT_RESUME, help="Resume PDF to attach.")
    p.add_argument("--subject", default="Following Up - Application for DevOps/Cloud Engineer Role",
                   help="Follow-up subject line.")
    p.add_argument("--window", type=int, default=WINDOW,
                   help="Days a cohort waits before it is followed up.")
    p.add_argument("--max-files", type=int, default=1,
                   help="Archived days to follow up per run (0 = every one that is due). "
                        "Default 1 keeps it to one cohort a day.")
    p.add_argument("--max-send", type=int, default=0, help="Cap emails this run (0 = no cap).")
    p.add_argument("--delay", type=float, default=5.0, help="Seconds between sends.")
    p.add_argument("--opens-file", default=os.getenv("TRACKING_OPENS_URL", ""),
                   help="JSON file/URL listing addresses (or pixel tokens) that were opened.")
    p.add_argument("--keep-current", action="store_true",
                   help="Also follow up addresses that are in today's file (default: skip them, "
                        "they were just mailed).")
    p.add_argument("--no-imap", action="store_true", help="Skip the reply/bounce scan.")
    p.add_argument("--archive-only", action="store_true", help="Archive today's file and stop.")
    p.add_argument("--send-only", action="store_true", help="Skip archiving, just follow up.")
    p.add_argument("--status", action="store_true", help="Print the folder and exit.")
    p.add_argument("--dry-run", action="store_true", help="Show the plan, change nothing.")
    return p.parse_args(argv)


# --------------------------------------------------------------------------- #
# the rolling 7-day folder
# --------------------------------------------------------------------------- #

def source_date(path):
    """The date the original file is for - its own ``date`` field, else today."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            stamp = json.load(fh).get("date", "")
        return datetime.strptime(stamp, "%Y-%m-%d").date()
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return date.today()


def archived(archive_dir, extra=()):
    """``(stamp, path)`` for every archived file, oldest first.

    ``extra`` lets a dry run pretend today's file is already in there.
    """
    found = []
    if os.path.isdir(archive_dir):
        for name in sorted(os.listdir(archive_dir)):
            match = ARCHIVE_RE.match(name)
            if match:
                found.append((match.group(1), os.path.join(archive_dir, name)))
    stamps = {stamp for stamp, _ in found}
    found.extend(item for item in extra if item[0] not in stamps)
    return sorted(found)


def archive_source(source, archive_dir, dry_run=False):
    """Copy today's original file into the rolling folder. Idempotent."""
    if not os.path.exists(source):
        print(f"! no source file at {source} - nothing to archive.")
        return None
    stamp = source_date(source).isoformat()
    dest = os.path.join(archive_dir, ARCHIVE_NAME.format(stamp))
    if os.path.exists(dest):
        print(f"= {stamp} already archived.")
    elif dry_run:
        print(f"+ WOULD archive today's file -> {os.path.basename(dest)}")
    else:
        os.makedirs(archive_dir, exist_ok=True)
        shutil.copyfile(source, dest)
        print(f"+ archived today's file -> {os.path.basename(dest)}")
    return dest


def age_of(stamp, today=None):
    return ((today or date.today()) - datetime.strptime(stamp, "%Y-%m-%d").date()).days


def due(files, window, today=None):
    """``(stamp, path, age)`` for every cohort that has waited ``window`` days."""
    return [(stamp, path, age_of(stamp, today))
            for stamp, path in files
            if age_of(stamp, today) >= window]


def prune(archive_dir, window, dry_run=False):
    """Safety net: abandon cohorts so old that following them up is pointless.

    Age-based on purpose. The old rule dropped the oldest file whenever the
    folder held more than ``window`` files, which deleted cohorts that were
    still queued for their follow-up.
    """
    for stamp, path, age in due(archived(archive_dir), window + STALE_AFTER):
        if dry_run:
            print(f"- WOULD drop stale {os.path.basename(path)} "
                  f"({age} day(s) old, never followed up)")
        else:
            os.remove(path)
            print(f"- dropped stale {os.path.basename(path)} "
                  f"({age} day(s) old, never followed up)")


def recipients_from(path):
    """HR addresses + locations out of a job file - same rule as ``extract.py``."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    out, seen = [], set()
    for job in data.get("jobs", []):
        location = job.get("locations", "") or job.get("location", "")
        for email in str(job.get("emails", "")).split(";"):
            email = email.strip()
            if not email or email.lower() in seen:
                continue
            seen.add(email.lower())
            out.append({"email": email, "location": location})
    return out


# --------------------------------------------------------------------------- #
# who has already engaged
# --------------------------------------------------------------------------- #

def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def scan_mailbox(user, password, since, host="imap.gmail.com"):
    """``(replied, bounced)`` lowercase address sets, from mail since ``since``."""
    replied, bounced = set(), set()
    since_str = since.strftime("%d-%b-%Y")
    box = imaplib.IMAP4_SSL(host)
    try:
        box.login(user, password)
        box.select("INBOX", readonly=True)

        # Anyone who wrote to you since the original send has engaged with it.
        typ, data = box.search(None, "SINCE", since_str)
        ids = data[0].split() if typ == "OK" and data and data[0] else []
        for group in _chunks(ids, 200):
            typ, resp = box.fetch(b",".join(group), "(BODY.PEEK[HEADER.FIELDS (FROM)])")
            for item in resp or []:
                if not (isinstance(item, tuple) and item[1]):
                    continue
                addr = parseaddr(message_from_bytes(item[1]).get("From", ""))[1].lower()
                if addr:
                    replied.add(addr)

        # Bounce notices: an address quoted in one never reached a mailbox.
        for who in BOUNCE_SENDERS:
            typ, data = box.search(None, "SINCE", since_str, "FROM", who)
            ids = data[0].split() if typ == "OK" and data and data[0] else []
            for msg_id in ids:
                typ, resp = box.fetch(msg_id, "(BODY.PEEK[])")
                for item in resp or []:
                    if isinstance(item, tuple) and item[1]:
                        body = item[1].decode("utf-8", "replace").lower()
                        bounced.update(EMAIL_RE.findall(body))
    finally:
        try:
            box.logout()
        except Exception:  # noqa: BLE001 - a failed logout changes nothing
            pass
    return replied, bounced


def pixel_token(email):
    """Stable open-tracking token for an address (matches ``send_mail.py``)."""
    return hashlib.sha1(email.strip().lower().encode("utf-8")).hexdigest()[:16]


def opened_set(source):
    """Addresses/tokens an open-tracker reports as opened. Empty when unconfigured."""
    if not source:
        return set()
    try:
        if source.startswith(("http://", "https://")):
            import urllib.request
            with urllib.request.urlopen(source, timeout=20) as resp:
                data = json.load(resp)
        else:
            with open(source, "r", encoding="utf-8") as fh:
                data = json.load(fh)
    except Exception as exc:  # noqa: BLE001 - a dead tracker must not block the run
        print(f"! open-tracking lookup failed ({exc}); treating everyone as unopened.")
        return set()
    if isinstance(data, dict):
        data = data.get("opened", data.get("opens", []))
    return {str(x).strip().lower() for x in (data or [])}


# --------------------------------------------------------------------------- #
# sending
# --------------------------------------------------------------------------- #

def build_message(sender, rec, subject, html, resume, sent_on, days):
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = rec["email"]
    msg["Subject"] = subject

    body = (html
            .replace("{{location}}", str(rec.get("location", "")))
            .replace("{{sent_date}}", sent_on.strftime("%d %b %Y"))
            .replace("{{days}}", str(days)))
    msg.attach(MIMEText(body, "html"))

    with open(resume, "rb") as fh:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(fh.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition",
                    f"attachment; filename={os.path.basename(resume)}")
    msg.attach(part)
    return msg


def send_cohort(args, stamp, path, age, user, password,
                mailed_today, opened, replied, bounced, budget):
    """Follow up one archived day, then retire its file. ``(rc, sent)``."""
    sent_on = datetime.strptime(stamp, "%Y-%m-%d").date()
    print(f"\nDue cohort: {os.path.basename(path)} (mailed {stamp}, {age} day(s) ago)")

    candidates = recipients_from(path)
    print(f"  {len(candidates)} address(es) were mailed that day")

    targets, skipped = [], []
    for rec in candidates:
        addr = rec["email"].lower()
        if addr in opened or pixel_token(addr) in opened:
            skipped.append((rec["email"], "already opened"))
        elif addr in bounced:
            skipped.append((rec["email"], "bounced - never delivered"))
        elif addr in replied:
            skipped.append((rec["email"], "already replied"))
        elif addr in mailed_today:
            skipped.append((rec["email"], "mailed again today"))
        else:
            targets.append(rec)

    for email, why in skipped:
        print(f"  skip {email:<38} ({why})")
    if budget is not None and len(targets) > budget:
        print(f"  capping at --max-send budget {budget} (of {len(targets)})")
        targets = targets[:budget]

    print(f"  -> {len(targets)} follow-up(s) to send"
          f"{' [DRY RUN]' if args.dry_run else ''}")

    if args.dry_run:
        for i, rec in enumerate(targets, 1):
            print(f"  {i:>2}. WOULD follow up {rec['email']:<38} ({rec.get('location', '')})")
        print(f"Dry run - {os.path.basename(path)} kept in the folder.")
        return 0, len(targets)

    sent = 0
    if targets:
        if not (user and password):
            print("ERROR: EMAIL_ADDRESS / EMAIL_PASSWORD not set. Aborting.", file=sys.stderr)
            return 1, 0
        if not os.path.exists(args.resume):
            print(f"ERROR: resume not found at {args.resume}. Aborting.", file=sys.stderr)
            return 1, 0
        with open(args.template, "r", encoding="utf-8") as fh:
            html = fh.read()

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(user, password)
        print("  logged into Gmail")

        failed = 0
        for i, rec in enumerate(targets, 1):
            try:
                msg = build_message(user, rec, args.subject, html, args.resume, sent_on, age)
                server.sendmail(user, rec["email"], msg.as_string())
                sent += 1
                print(f"  {i:>2}. sent -> {rec['email']}")
            except Exception as exc:  # noqa: BLE001 - keep going past single failures
                failed += 1
                print(f"  {i:>2}. FAILED {rec['email']}: {exc}")
            if i < len(targets):
                time.sleep(args.delay)
        server.quit()
        print(f"  follow-ups sent {sent}, failed {failed}")
    else:
        print("  nobody left to follow up - retiring the file anyway.")

    # This cohort is done: drop its file so the next-oldest takes its turn.
    if os.path.exists(path) and os.path.dirname(path) == os.path.abspath(args.archive_dir):
        os.remove(path)
        print(f"- retired {os.path.basename(path)}")
    return 0, sent


def follow_up(args, pending=()):
    """Follow up every cohort that has waited ``--window`` days, oldest first."""
    files = archived(args.archive_dir, pending)
    ready = due(files, args.window)
    if not ready:
        if files:
            oldest_stamp = files[0][0]
            waiting = args.window - age_of(oldest_stamp)
            print(f"\nNothing is {args.window} day(s) old yet - {len(files)} file(s) held, "
                  f"oldest is {oldest_stamp} ({waiting} more day(s) to wait).")
        else:
            print("\nArchive is empty - nothing to follow up.")
        return 0

    print(f"\n{len(ready)} cohort(s) at or past {args.window} day(s): "
          + ", ".join(stamp for stamp, _, _ in ready))
    if args.max_files > 0 and len(ready) > args.max_files:
        print(f"  handling the oldest {args.max_files} this run "
              f"(--max-files); the rest go out on the following runs.")
        ready = ready[: args.max_files]

    # Skip anyone who is in today's original mail - they just heard from you.
    mailed_today = set()
    if not args.keep_current and os.path.exists(args.source):
        mailed_today = {r["email"].lower() for r in recipients_from(args.source)}

    opened = opened_set(args.opens_file)
    if opened:
        print(f"  open-tracker reports {len(opened)} opened entr(ies)")

    user = os.getenv("EMAIL_ADDRESS")
    password = os.getenv("EMAIL_PASSWORD")

    # One mailbox scan covers every cohort - search back to the oldest send.
    replied, bounced = set(), set()
    if args.no_imap:
        print("  (--no-imap: reply/bounce scan skipped)")
    elif user and password:
        since = datetime.strptime(ready[0][0], "%Y-%m-%d").date()
        try:
            replied, bounced = scan_mailbox(user, password, since)
            print(f"  mailbox scan since {since}: {len(replied)} sender(s) wrote back, "
                  f"{len(bounced)} address(es) bounced")
        except Exception as exc:  # noqa: BLE001 - report and stop, never guess
            print(f"ERROR: mailbox scan failed ({exc}). Without it there is no way to "
                  "tell who already replied, so nothing is sent; the files stay put "
                  "for tomorrow's retry. Use --no-imap to follow up without the check.",
                  file=sys.stderr)
            return 1
    else:
        print("! EMAIL_ADDRESS / EMAIL_PASSWORD unset - cannot scan for replies.")

    budget = args.max_send if args.max_send > 0 else None
    for stamp, path, age in ready:
        if budget == 0:
            print(f"\n--max-send budget spent - {os.path.basename(path)} waits for the next run.")
            break
        rc, sent = send_cohort(args, stamp, path, age, user, password,
                               mailed_today, opened, replied, bounced, budget)
        if rc != 0:
            return rc
        if budget is not None:
            budget -= sent
    return 0


def show_status(args, pending=()):
    files = archived(args.archive_dir, pending)
    print(f"\nArchive: {args.archive_dir}")
    if not files:
        print("  (empty)")
    ready = 0
    for i, (stamp, path) in enumerate(files, 1):
        age = age_of(stamp)
        try:
            count = len(recipients_from(path))
        except (OSError, json.JSONDecodeError):
            count = 0
        if age >= args.window:
            ready += 1
            tag = "   <- DUE" + (" (goes out next)" if ready == 1 else "")
        else:
            tag = f"   waits {args.window - age} more day(s)"
        print(f"  {i}. jobs_{stamp}.json  {age} day(s) old  {count} address(es){tag}")
    print(f"  {len(files)} file(s) held, {ready} at or past {args.window} day(s) - "
          + ("ready to follow up." if ready else "nothing due yet."))


def main(argv=None):
    args = parse_args(argv)
    args.archive_dir = os.path.abspath(args.archive_dir)

    if args.status:
        show_status(args)
        return 0

    pending = ()
    if not args.send_only:
        dest = archive_source(args.source, args.archive_dir, args.dry_run)
        if args.dry_run and dest and not os.path.exists(dest):
            # Point the pretend entry at the real source so it can still be read.
            pending = ((source_date(args.source).isoformat(), args.source),)

    if args.archive_only:
        show_status(args, pending)
        return 0

    rc = follow_up(args, pending)
    if rc == 0:
        # Only bound the folder after a clean run - pruning a cohort whose send
        # just failed would lose it for good instead of retrying tomorrow.
        prune(args.archive_dir, args.window, args.dry_run)
    else:
        print("! run failed - folder left as-is so tomorrow can retry.")
    show_status(args, pending if args.dry_run else ())
    return rc


if __name__ == "__main__":
    sys.exit(main())
