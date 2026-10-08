"""SQLite storage: jobs, applications, contacted addresses, the Q&A cache and
the questions still waiting for an answer.

One small file (data/autopilot.db) is the tool's whole memory: it is what
stops a job, a role or a recruiter from being hit twice, and what lets a
question answered once never be asked again.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autopilot.models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT, external_id TEXT, title TEXT, company TEXT, location TEXT, country TEXT,
    url TEXT, apply_url TEXT, apply_type TEXT, hr_email TEXT, is_remote INTEGER,
    date_posted TEXT, description TEXT, experience_text TEXT,
    salary_min REAL, salary_max REAL, salary_currency TEXT, salary_interval TEXT,
    fingerprint TEXT, tier INTEGER, score REAL, matched TEXT, missing TEXT, flags TEXT,
    status TEXT NOT NULL DEFAULT 'new', status_reason TEXT, package_dir TEXT,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, seen_count INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS jobs_fingerprint ON jobs(fingerprint);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    recipient TEXT, resume_path TEXT, detail TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS applications_job ON applications(job_id);

CREATE TABLE IF NOT EXISTS contacts (
    email TEXT PRIMARY KEY,
    last_contacted TEXT NOT NULL,
    job_id TEXT,
    times INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS answers (
    key TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'you',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_questions (
    key TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    field_type TEXT,
    options TEXT,
    job_id TEXT,
    first_asked TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    mode TEXT,
    stats TEXT
);
"""

# Statuses that mean "we already acted on this job; leave it alone".
DONE_STATUSES = ("applied", "manual", "needs_attention")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def question_key(question: str) -> str:
    """Normalise a form question so trivial differences don't defeat the cache.

    "Notice period (in days)?*" and "notice period in days" share one key.
    """
    low = re.sub(r"\(.*?optional.*?\)", " ", (question or "").lower())
    low = re.sub(r"[^a-z0-9]+", " ", low)
    return re.sub(r"\s+", " ", low).strip()[:200]


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ jobs
    def upsert_job(self, job: Job, status: str | None = None, reason: str = "") -> None:
        """Insert or refresh a job. Re-sightings bump seen_count/last_seen,
        which the genuineness filter uses to spot ghost postings."""
        now = now_iso()
        row = self.conn.execute("SELECT status FROM jobs WHERE id = ?", (job.id,)).fetchone()
        fields = {
            "source": job.source, "external_id": job.external_id, "title": job.title,
            "company": job.company, "location": job.location, "country": job.country,
            "url": job.url, "apply_url": job.apply_url, "apply_type": job.apply_type,
            "hr_email": job.hr_email, "is_remote": int(bool(job.is_remote)),
            "date_posted": job.date_posted.isoformat() if job.date_posted else None,
            "description": job.description, "experience_text": job.experience_text,
            "salary_min": job.salary_min, "salary_max": job.salary_max,
            "salary_currency": job.salary_currency, "salary_interval": job.salary_interval,
            "fingerprint": job.fingerprint, "tier": job.tier, "score": round(job.score, 4),
            "matched": json.dumps(job.matched), "missing": json.dumps(job.missing),
            "flags": json.dumps(job.flags),
        }
        if row is None:
            fields.update(id=job.id, status=status or "new", status_reason=reason,
                          first_seen=now, last_seen=now, seen_count=1)
            cols = ", ".join(fields)
            marks = ", ".join("?" for _ in fields)
            self.conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({marks})", list(fields.values()))
        else:
            # Never downgrade a job we already acted on back to new/queued.
            keep = row["status"] in DONE_STATUSES
            if status and not keep:
                fields.update(status=status, status_reason=reason)
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.conn.execute(
                f"UPDATE jobs SET {sets}, last_seen = ?, seen_count = seen_count + 1 WHERE id = ?",
                [*fields.values(), now, job.id],
            )
        self.conn.commit()

    def set_status(self, job_id: str, status: str, reason: str = "", package_dir: str | None = None) -> None:
        if package_dir is None:
            self.conn.execute("UPDATE jobs SET status = ?, status_reason = ? WHERE id = ?",
                              (status, reason, job_id))
        else:
            self.conn.execute("UPDATE jobs SET status = ?, status_reason = ?, package_dir = ? WHERE id = ?",
                              (status, reason, package_dir, job_id))
        self.conn.commit()

    def update_route(self, job: Job) -> None:
        """Store a better way to apply found after screening (see discovery/resolve.py)."""
        self.conn.execute("UPDATE jobs SET apply_type = ?, apply_url = ?, flags = ? WHERE id = ?",
                          (job.apply_type, job.apply_url, json.dumps(job.flags), job.id))
        self.conn.commit()

    def job_row(self, job_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def jobs_by_status(self, *statuses: str) -> list[sqlite3.Row]:
        marks = ", ".join("?" for _ in statuses)
        return self.conn.execute(
            f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY tier, score DESC", statuses
        ).fetchall()

    def first_seen_for_fingerprint(self, fingerprint: str) -> datetime | None:
        row = self.conn.execute("SELECT MIN(first_seen) AS t FROM jobs WHERE fingerprint = ?",
                                (fingerprint,)).fetchone()
        return datetime.fromisoformat(row["t"]) if row and row["t"] else None

    def handled(self, job: Job) -> str:
        """Why this job must not be applied to again ('' if it may be)."""
        row = self.job_row(job.id)
        if row and row["status"] in DONE_STATUSES:
            return f"already {row['status'].replace('_', ' ')}"
        since = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        hit = self.conn.execute(
            "SELECT 1 FROM jobs WHERE fingerprint = ? AND id != ? AND status = 'applied' "
            "AND last_seen >= ? LIMIT 1", (job.fingerprint, job.id, since),
        ).fetchone()
        return "already applied to this role at this company" if hit else ""

    # ---------------------------------------------------------- applications
    def record_application(self, job_id: str, channel: str, status: str, recipient: str = "",
                           resume_path: str = "", detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO applications (job_id, channel, status, recipient, resume_path, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (job_id, channel, status, recipient, resume_path, detail, now_iso()),
        )
        self.conn.commit()

    def applications(self, since: str | None = None) -> list[sqlite3.Row]:
        sql = ("SELECT a.*, j.title, j.company, j.location, j.url, j.apply_url, j.tier, j.score, j.source "
               "FROM applications a LEFT JOIN jobs j ON j.id = a.job_id")
        if since:
            return self.conn.execute(sql + " WHERE a.created_at >= ? ORDER BY a.id", (since,)).fetchall()
        return self.conn.execute(sql + " ORDER BY a.id").fetchall()

    # -------------------------------------------------------------- contacts
    def last_contacted(self, email: str) -> datetime | None:
        row = self.conn.execute("SELECT last_contacted FROM contacts WHERE email = ?",
                                (email.lower(),)).fetchone()
        return datetime.fromisoformat(row["last_contacted"]) if row else None

    def record_contact(self, email: str, job_id: str) -> None:
        self.conn.execute(
            "INSERT INTO contacts (email, last_contacted, job_id) VALUES (?, ?, ?) "
            "ON CONFLICT(email) DO UPDATE SET last_contacted = excluded.last_contacted, "
            "job_id = excluded.job_id, times = times + 1",
            (email.lower(), now_iso(), job_id),
        )
        self.conn.commit()

    # --------------------------------------------------------------- answers
    def get_answer(self, question: str) -> str | None:
        row = self.conn.execute("SELECT answer FROM answers WHERE key = ?",
                                (question_key(question),)).fetchone()
        return row["answer"] if row else None

    def set_answer(self, question: str, answer: str, source: str = "you") -> None:
        key = question_key(question)
        self.conn.execute(
            "INSERT INTO answers (key, question, answer, source, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET answer = excluded.answer, source = excluded.source, "
            "updated_at = excluded.updated_at",
            (key, question.strip(), str(answer), source, now_iso()),
        )
        self.conn.execute("DELETE FROM pending_questions WHERE key = ?", (key,))
        self.conn.commit()

    def delete_answer(self, question: str) -> bool:
        cur = self.conn.execute("DELETE FROM answers WHERE key = ?", (question_key(question),))
        self.conn.commit()
        return cur.rowcount > 0

    def all_answers(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM answers ORDER BY question").fetchall()

    def add_pending(self, question: str, field_type: str, options: list[str], job_id: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO pending_questions (key, question, field_type, options, job_id, first_asked) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (question_key(question), question.strip(), field_type, json.dumps(options or []), job_id, now_iso()),
        )
        self.conn.commit()

    def pending(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM pending_questions ORDER BY first_asked").fetchall()

    # ------------------------------------------------------------------ runs
    def start_run(self, mode: str) -> int:
        cur = self.conn.execute("INSERT INTO runs (started_at, mode) VALUES (?, ?)", (now_iso(), mode))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, stats: dict) -> None:
        self.conn.execute("UPDATE runs SET finished_at = ?, stats = ? WHERE id = ?",
                          (now_iso(), json.dumps(stats), run_id))
        self.conn.commit()

    def run_started(self, run_id: int) -> str:
        row = self.conn.execute("SELECT started_at FROM runs WHERE id = ?", (run_id,)).fetchone()
        return row["started_at"] if row else now_iso()

    def status_counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    def reject_reasons(self, limit: int = 12) -> list[tuple[str, int]]:
        rows = self.conn.execute(
            "SELECT status_reason AS r, COUNT(*) AS n FROM jobs WHERE status = 'rejected' "
            "GROUP BY status_reason ORDER BY n DESC LIMIT ?", (limit,)
        ).fetchall()
        return [(r["r"], r["n"]) for r in rows]
