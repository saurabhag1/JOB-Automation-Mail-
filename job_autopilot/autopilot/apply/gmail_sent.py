"""Everyone your Gmail account emailed recently, read from the Sent folder over IMAP.

Local runs started from the portal set AUTOPILOT_GMAIL_DEDUPE=1: the GitHub Actions runs keep their
"already emailed" history in the Actions cache, which this Mac cannot see, but every one of them
sends from the same Gmail account - so its Sent folder is the complete list. Only the To / Cc / Bcc
headers are read (BODY.PEEK: nothing is marked read). GitHub Actions never sets the variable.
"""

from __future__ import annotations

import email.utils
import imaplib
import os
from datetime import datetime, timedelta

_cache: dict[int, set[str]] = {}


def enabled() -> bool:
    return os.getenv("AUTOPILOT_GMAIL_DEDUPE", "") == "1"


def sent_recipients(days: int = 60, log=print) -> set[str]:
    if days in _cache:
        return _cache[days]
    user, password = os.getenv("EMAIL_ADDRESS", ""), os.getenv("EMAIL_PASSWORD", "")
    found: set[str] = set()
    if not (user and password):
        return found
    try:
        imap = imaplib.IMAP4_SSL(os.getenv("IMAP_HOST", "imap.gmail.com"), timeout=60)
        imap.login(user, password)
        status, _ = imap.select('"[Gmail]/Sent Mail"', readonly=True)
        if status != "OK":
            imap.select("Sent", readonly=True)
        since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        _, data = imap.search(None, "SINCE", since)
        ids = data[0].split() if data and data[0] else []
        for start in range(0, len(ids), 300):
            chunk = b",".join(ids[start:start + 300]).decode()
            _, rows = imap.fetch(chunk, "(BODY.PEEK[HEADER.FIELDS (TO CC BCC)])")
            for row in rows:
                if isinstance(row, tuple):
                    headers = row[1].decode("utf-8", "replace").replace("\r\n ", " ")
                    values = [line.split(":", 1)[1] for line in headers.splitlines() if ":" in line]
                    found |= {addr.lower() for _, addr in email.utils.getaddresses(values) if "@" in addr}
        imap.logout()
        found.discard(user.lower())
        log(f"Gmail Sent folder: {len(found)} addresses emailed in the last {days} days will not be emailed again")
    except Exception as exc:  # noqa: BLE001 - dedupe is best effort; the other lists still apply
        log(f"! could not read the Gmail Sent folder ({type(exc).__name__}) - IMAP may be off in Gmail settings")
    _cache[days] = found
    return found
