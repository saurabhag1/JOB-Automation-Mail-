"""Cross-run dedupe state.

Keeps a JSON file of emails already used in previous runs so that every daily
run yields fresh, unique leads. Small and dependency-free on purpose.
"""

import json
import os

import config


def load_state(path=None):
    path = path or config.STATE_FILE
    if not os.path.exists(path):
        return {"seen_emails": [], "last_run": None}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {"seen_emails": [], "last_run": None}
    data.setdefault("seen_emails", [])
    data.setdefault("last_run", None)
    return data


def save_state(state, path=None):
    path = path or config.STATE_FILE
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def seen_set(state):
    return {e.lower() for e in state.get("seen_emails", [])}


def mark_seen(state, emails):
    bucket = state.setdefault("seen_emails", [])
    existing = {e.lower() for e in bucket}
    for email in emails:
        if email and email.lower() not in existing:
            bucket.append(email.lower())
            existing.add(email.lower())
