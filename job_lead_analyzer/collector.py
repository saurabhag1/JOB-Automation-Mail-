#!/usr/bin/env python3
"""Job Lead Analyzer -- daily collector.

Scrapes public job posts, extracts genuine (reply-capable) HR emails for
DevOps/AWS roles, filters by experience + skills, dedupes against previous runs,
and writes a JSON file of fresh, unique leads.

Run:
    python3 collector.py --target-count 10
    python3 collector.py --target-count 10 --no-serpapi   # free feeds only
"""

import argparse
import datetime
import json
import re
import sys

from dateutil import parser as date_parser
from dotenv import load_dotenv

import config
import extractor
import state as state_mod
from sources import feeds_source, serpapi_source

load_dotenv()


def _utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _today():
    return datetime.date.today().isoformat()


_REL_DATE = re.compile(r"(\d+)\s*(minute|hour|day|week|month|year)s?\s*ago", re.IGNORECASE)
_REL_UNIT_DAYS = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}


def _relative_age_days(text):
    """Parse Google-style relative dates ('7 days ago', 'yesterday') -> age in days.

    Returns None if the string isn't a recognizable relative date.
    """
    low = text.strip().lower()
    if low in ("today", "just now", "now"):
        return 0
    if low == "yesterday":
        return 1
    m = _REL_DATE.search(low)
    if m:
        return int(m.group(1)) * _REL_UNIT_DAYS[m.group(2).lower()]
    return None


def is_recent(date_str, max_age_days):
    """True if *date_str* is within max_age_days of now.

    Handles unix epochs, ISO/absolute dates, and relative strings like
    '7 days ago'. Unparseable / missing dates return True (we can't prove the
    post is old, so we keep it rather than drop a possibly-good lead).
    """
    if not date_str:
        return True
    date_str = str(date_str)

    rel = _relative_age_days(date_str)
    if rel is not None:
        return rel <= max_age_days

    try:
        if date_str.isdigit():  # unix epoch (Arbeitnow)
            dt = datetime.datetime.fromtimestamp(int(date_str), datetime.timezone.utc)
        else:
            dt = date_parser.parse(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
    except (ValueError, OverflowError, TypeError):
        return True
    age = datetime.datetime.now(datetime.timezone.utc) - dt
    return age <= datetime.timedelta(days=max_age_days)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Daily DevOps/AWS HR-email job lead collector.")
    p.add_argument("--target-count", type=int, default=config.TARGET_COUNT,
                   help=f"How many fresh leads to collect (default {config.TARGET_COUNT}).")
    p.add_argument("--exp-min", type=int, default=config.EXPERIENCE_MIN,
                   help=f"Minimum years of experience (default {config.EXPERIENCE_MIN}).")
    p.add_argument("--exp-max", type=int, default=config.EXPERIENCE_MAX,
                   help=f"Maximum years of experience (default {config.EXPERIENCE_MAX}).")
    p.add_argument("--max-searches", type=int, default=config.MAX_SEARCHES,
                   help=f"SerpApi searches per run (default {config.MAX_SEARCHES}).")
    p.add_argument("--days", type=int, default=config.RECENCY_DAYS,
                   help=f"Only keep posts from the last N days (default {config.RECENCY_DAYS}).")
    p.add_argument("--locations", type=str, default="",
                   help="Comma-separated search locations (overrides config).")
    p.add_argument("--no-serpapi", action="store_true",
                   help="Skip SerpApi; use free job feeds only (no key, lower yield).")
    p.add_argument("--allow-generic", action="store_true", default=True,
                   help="Allow generic hr@/careers@ as fallback (on by default).")
    p.add_argument("--personal-only", dest="allow_generic", action="store_false",
                   help="Only accept named-person emails; drop generic inboxes.")
    p.add_argument("--out", type=str, default=config.OUTPUT_JSON,
                   help=f"Output JSON path (default {config.OUTPUT_JSON}).")
    p.add_argument("--state", type=str, default=config.STATE_FILE,
                   help=f"Dedupe state file path (default {config.STATE_FILE}).")
    p.add_argument("--no-dedupe", action="store_true",
                   help="Ignore the state file (useful for testing).")
    return p.parse_args(argv)


def gather_posts(args):
    """Collect raw posts from the configured sources."""
    locations = [l.strip() for l in args.locations.split(",") if l.strip()] or None
    posts = []
    if not args.no_serpapi:
        print("Source: SerpApi (public hiring posts)")
        posts.extend(serpapi_source.collect(locations=locations, max_searches=args.max_searches))
    print("Source: free job feeds")
    posts.extend(feeds_source.collect(query="devops"))
    return posts


def build_lead(post, args):
    """Turn a raw post into a slim lead record, or None if it doesn't qualify."""
    text = post.get("text", "")

    picked = extractor.pick_emails(text)
    if picked["primary"] is None:          # no complete, reply-capable email -> skip job
        return None
    if picked["email_type"] == "generic" and not args.allow_generic:
        return None

    exp_min, exp_max = extractor.parse_experience(text)
    if not extractor.experience_overlaps(exp_min, exp_max, args.exp_min, args.exp_max):
        return None

    matched = extractor.match_skills(text)
    if not extractor.is_relevant(matched):
        return None  # not a genuine DevOps/Cloud role matching your skills

    locations = extractor.guess_locations(text) or post.get("location", "")
    company = extractor.guess_company(text, picked["primary"], post.get("company", ""))
    phone = extractor.extract_phone(text)
    position = extractor.clean_title(post.get("title", "")) or _infer_role(text)

    # Short, clean record — only the fields you act on.
    return {
        "company": company,
        "position": position,
        "email": picked["primary"],
        "alternateEmails": picked["alternates"],
        "phone": phone,
        "location": locations,
        "experience": extractor.format_experience(exp_min, exp_max),
        "skills": matched,
        "emailType": picked["email_type"],
        "datePosted": post.get("date_posted", ""),
        "source": post.get("source", ""),
        "url": post.get("url", ""),
    }


def _infer_role(text):
    low = (text or "").lower()
    for role in config.ROLE_KEYWORDS:
        if role in low:
            return role.title()
    return "DevOps Engineer"


def rank_key(lead):
    """Sort key: personal email first, then priority location, then more skills."""
    type_rank = 0 if lead["emailType"] == "personal" else 1
    loc_rank = extractor.location_rank(lead["location"])
    return (type_rank, loc_rank, -len(lead["skills"]))


def main(argv=None):
    args = parse_args(argv)
    print(f"=== Job Lead Analyzer | {_today()} | target {args.target_count} leads ===")

    st = state_mod.load_state(args.state)
    seen = set() if args.no_dedupe else state_mod.seen_set(st)

    posts = gather_posts(args)
    print(f"Total raw posts gathered: {len(posts)}")

    # Keep only recent posts (last N days) when the source gives us a date.
    posts = [p for p in posts if is_recent(p.get("date_posted", ""), args.days)]
    print(f"After recency filter (<= {args.days} days): {len(posts)}")

    leads = []
    used_emails = set()
    for post in posts:
        lead = build_lead(post, args)
        if not lead:
            continue
        primary = lead["email"].lower()
        if primary in seen or primary in used_emails:
            continue  # already used in a previous run or earlier in this run
        used_emails.add(primary)
        leads.append(lead)

    leads.sort(key=rank_key)
    selected = leads[: args.target_count]

    payload = {
        "generatedOn": _today(),
        "experienceRange": f"{args.exp_min}-{args.exp_max} yrs",
        "maxAgeDays": args.days,
        "totalLeads": len(selected),
        "leads": selected,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    if not args.no_dedupe:
        state_mod.mark_seen(st, [l["email"] for l in selected])
        st["last_run"] = _utc_now_iso()
        state_mod.save_state(st, args.state)

    print(f"\nQualified leads found this run: {len(leads)}")
    print(f"Selected (new & unique): {len(selected)} -> written to {args.out}")
    if selected:
        print("\nLeads:")
        for i, l in enumerate(selected, 1):
            print(f"  {i:>2}. {l['email']:<38} [{l['emailType']:<8}] "
                  f"{l['position']:<22} {l['location']}")
    else:
        print("No new leads this run. With your SERPAPI_KEY set, try raising "
              "--max-searches or --days, or run again later.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
