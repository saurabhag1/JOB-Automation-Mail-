"""Find the employer's own Greenhouse / Lever / Ashby form for jobs that would
otherwise be handed to you as a link, so the bot can apply to them itself.

Two read-only checks, in order:
* the posting's own page links to or embeds the form - company career sites
  built on these systems (``?gh_jid=``, an embedded Ashby board, a single
  ``jobs.lever.co`` link);
* the company's public job board on Greenhouse / Lever / Ashby (board name
  guessed from the company name) lists the same title in the same place.
  This is how LinkedIn "apply on company site" jobs get a form: LinkedIn only
  shows that link to signed-in users.

A job is only re-routed when the title AND the location match, so a guessed
board name that belongs to another company cannot redirect an application.
"""

from __future__ import annotations

import difflib
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from autopilot.config import Settings
from autopilot.discovery.common import TIMEOUT, detect_ats, session
from autopilot.models import (APPLY_ASHBY, APPLY_EXTERNAL, APPLY_GREENHOUSE, APPLY_LEVER, APPLY_LINKEDIN_OFFSITE,
                              Job, norm_company, norm_title)

# Hosts whose pages never carry the employer's form (aggregators, or blocked to bots).
_AGGREGATORS = re.compile(r"(?:^|\.)(?:linkedin|indeed|naukri|glassdoor|bayt|foundit|monster|instahyre|hirist|"
                          r"iimjobs|cutshort|wellfound|angel|shine|timesjobs|apna|dice|naukrigulf|google|"
                          r"remotive|remoteok|jobicy|simplyhired|ziprecruiter)\.", re.I)
_ATS_LINK = re.compile(r"https?://(?:job-boards|boards)\.greenhouse\.io/[^\s\"'<>\\]+|"
                       r"https?://jobs\.(?:eu\.)?lever\.co/[^\s\"'<>\\]+|"
                       r"https?://jobs\.ashbyhq\.com/[^\s\"'<>\\]+", re.I)
_GH_JID = re.compile(r"[?&]gh_jid=(\d+)")
_GH_BOARD = re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([\w.-]+)", re.I)
_ASHBY_JID = re.compile(r"[?&]ashby_jid=([0-9a-f]{8}-[0-9a-f-]{27,})", re.I)
_ASHBY_EMBED = re.compile(r"jobs\.ashbyhq\.com/([\w.-]+)/embed", re.I)
_GENERIC_PLACE = {"remote", "hybrid", "onsite", "on site", "anywhere", "worldwide", "global", "office", "in office"}
_UNKNOWN_COMPANY = {"", "company", "confidential", "hiring", "not stated", "stealth", "na", "n a"}


def _ats_url(kind: str, board: str, job_id: str) -> str:
    if kind == APPLY_GREENHOUSE:   # the embedded form loads even when the board redirects to the company site
        return f"https://job-boards.greenhouse.io/embed/job_app?for={board}&token={job_id}"
    if kind == APPLY_LEVER:
        return f"https://jobs.lever.co/{board}/{job_id}/apply"
    return f"https://jobs.ashbyhq.com/{board}/{job_id}/application"


# --------------------------------------------------------------------------- #
# 1. The posting's own page
# --------------------------------------------------------------------------- #
def page_route(url: str, sess) -> tuple[str, str, str] | None:
    """(apply_type, form URL, how) from the posting page, or None."""
    kind, form = detect_ats(url)
    if kind != APPLY_EXTERNAL:
        return kind, form, "the posting link is the form"
    host = urlsplit(url).netloc.lower()
    if not host or _AGGREGATORS.search(host + "."):
        return None
    try:
        resp = sess.get(url, timeout=TIMEOUT)
    except Exception:  # noqa: BLE001 - unreachable career site
        return None
    if not resp.ok:
        return None
    kind, form = detect_ats(resp.url)
    if kind != APPLY_EXTERNAL:
        return kind, form, "the career page redirects to the form"
    html = resp.text[:3_000_000]
    jid, board = _GH_JID.search(resp.url) or _GH_JID.search(url), _GH_BOARD.search(html)
    if jid and board:
        return APPLY_GREENHOUSE, _ats_url(APPLY_GREENHOUSE, board.group(1), jid.group(1)), "career page embeds Greenhouse"
    jid, board = _ASHBY_JID.search(resp.url) or _ASHBY_JID.search(url), _ASHBY_EMBED.search(html)
    if jid and board:
        return APPLY_ASHBY, _ats_url(APPLY_ASHBY, board.group(1), jid.group(1)), "career page embeds Ashby"
    forms = {detect_ats(link) for link in _ATS_LINK.findall(html)}
    forms = {f for f in forms if f[0] != APPLY_EXTERNAL}
    if len(forms) == 1:      # one job-specific form; a page listing many jobs is ambiguous
        kind, form = forms.pop()
        return kind, form, f"career page links to its {kind} form"
    return None


# --------------------------------------------------------------------------- #
# 2. The company's public board
# --------------------------------------------------------------------------- #
def board_names(company: str) -> list[str]:
    """Likely board tokens: 'Thermo Fisher Scientific' -> thermofisherscientific, thermo-fisher-scientific,
    and the full legal name squashed (Greenhouse tokens like razorpaysoftwareprivatelimited)."""
    base = norm_company(company)
    if base in _UNKNOWN_COMPANY:
        return []
    words = base.split()
    names = ["".join(words), "-".join(words), re.sub(r"[^a-z0-9]", "", (company or "").lower())]
    return [n for i, n in enumerate(names) if len(n) >= 3 and n not in names[:i]]


def _postings(kind: str, board: str, sess) -> list[dict]:
    """[{title, location, url}] from one public board; [] when it doesn't exist."""
    try:
        if kind == APPLY_GREENHOUSE:
            r = sess.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", timeout=TIMEOUT)
            items = r.json().get("jobs", []) if r.ok else []
            return [{"title": i.get("title", ""), "location": (i.get("location") or {}).get("name", ""),
                     "url": _ats_url(kind, board, str(i.get("id")))} for i in items if i.get("id")]
        if kind == APPLY_LEVER:
            r = sess.get(f"https://api.lever.co/v0/postings/{board}?mode=json", timeout=TIMEOUT)
            items = r.json() if r.ok else []
            out = []
            for p in items if isinstance(items, list) else []:
                cats = p.get("categories") or {}
                place = ", ".join([cats.get("location") or ""] + list(cats.get("allLocations") or []))
                if p.get("workplaceType") == "remote":
                    place += ", Remote"
                out.append({"title": p.get("text", ""), "location": place,
                            "url": p.get("applyUrl") or f"{p.get('hostedUrl', '')}/apply"})
            return out
        r = sess.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}", timeout=TIMEOUT)
        items = r.json().get("jobs", []) if r.ok else []
        out = []
        for p in items:
            if not p.get("isListed", True):
                continue
            places = [p.get("location") or ""] + [s.get("location", "") for s in p.get("secondaryLocations") or []]
            if p.get("isRemote"):
                places.append("Remote")
            out.append({"title": p.get("title", ""), "location": ", ".join(x for x in places if x),
                        "url": p.get("applyUrl") or f"{p.get('jobUrl', '')}/application"})
        return out
    except Exception:  # noqa: BLE001 - no such board / not JSON
        return []


def same_title(a: str, b: str) -> bool:
    na, nb = norm_title(a), norm_title(b)
    return bool(na) and (na == nb or difflib.SequenceMatcher(None, na, nb).ratio() >= 0.9)


def same_place(job: Job, posting_location: str) -> bool:
    """The posting is for the place the job ad named (a city, or the country if that's all it gave)."""
    pl = (posting_location or "").lower()
    if not pl:
        return False
    jl = (job.location or "").lower()
    if job.is_remote or "remote" in jl:
        return "remote" in pl
    parts = {p.strip() for p in re.split(r"[,/|;()]+|\s-\s", jl)}
    parts = {p for p in parts if len(p) >= 3 and p not in _GENERIC_PLACE}
    return any(re.search(rf"(?<![a-z]){re.escape(p)}(?![a-z])", pl) for p in parts)


class BoardIndex:
    """Board listings fetched at most once per run, shared by all jobs."""

    def __init__(self, sess):
        self.sess = sess
        self._cache: dict[tuple[str, str], list[dict]] = {}
        self._lock = threading.Lock()

    def postings(self, kind: str, board: str) -> list[dict]:
        key = (kind, board)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        found = _postings(kind, board, self.sess)
        with self._lock:
            self._cache[key] = found
        return found

    def route(self, job: Job) -> tuple[str, str, str] | None:
        for board in board_names(job.company):
            for kind in (APPLY_GREENHOUSE, APPLY_LEVER, APPLY_ASHBY):
                hits = [p for p in self.postings(kind, board)
                        if same_title(job.title, p["title"]) and same_place(job, p["location"])]
                if hits:
                    return kind, hits[0]["url"], f"same job on {job.company}'s {kind} board"
        return None


# --------------------------------------------------------------------------- #
def resolve_routes(jobs: list[Job], settings: Settings, db=None, log=print) -> int:
    """Re-route link-only jobs to their employer form where one exists. Returns how many."""
    limit = int(settings.get("apply.find_ats_forms.max_jobs", 60))
    todo = [j for j in jobs if not j.hr_email and j.apply_type in (APPLY_EXTERNAL, APPLY_LINKEDIN_OFFSITE)][:limit]
    if not todo:
        return 0
    sess = session()
    index = BoardIndex(sess)

    def one(job: Job):
        return page_route(job.apply_url or job.url, sess) or index.route(job)

    with ThreadPoolExecutor(max_workers=6) as pool:
        routes = list(pool.map(one, todo))
    found = 0
    for job, route in zip(todo, routes):
        if not route:
            continue
        kind, form, how = route
        job.apply_type, job.apply_url = kind, form
        job.flags.append(f"form found: {how}")
        found += 1
        if db is not None:
            db.update_route(job)
        log(f"  form found for {job.title} @ {job.company}: {how}")
    log(f"Employer forms found for {found} of {len(todo)} link-only jobs (the bot applies to those itself).")
    return found
