"""An encrypted copy of your private files, so the daily GitHub Actions run has
the same memory as this Mac without publishing anything (the repo is public).

    job_autopilot/vault/private.bin   committed; unreadable without AUTOPILOT_VAULT_KEY

It holds profile.yaml, data/master_resume.json and data/autopilot.db - the
database that stops a recruiter being mailed twice and remembers form answers.

    python3 job_hunt.py vault save    pack the local files (do this after editing profile.yaml)
    python3 job_hunt.py vault load    restore every file in the vault that is newer than yours
    python3 job_hunt.py vault status  what is in it

Encryption: scrypt-derived key + Fernet (AES-128-CBC with HMAC-SHA256), from
the `cryptography` package. The passphrase lives only in .env and in the
repository secret AUTOPILOT_VAULT_KEY.
"""

from __future__ import annotations

import base64
import gzip
import io
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path

from autopilot.config import ROOT

MAGIC = b"JAVAULT1"
FILES = ("profile.yaml", "data/master_resume.json", "data/autopilot.db")
VAULT = ROOT / "vault" / "private.bin"
KEY_ENV = "AUTOPILOT_VAULT_KEY"


class VaultError(Exception):
    pass


def passphrase() -> str:
    key = (os.getenv(KEY_ENV) or "").strip()
    if len(key) < 16:
        raise VaultError(f"{KEY_ENV} is not set (or shorter than 16 characters) - add it to job_autopilot/.env "
                         "and to the repository's Actions secrets")
    return key


def _fernet(secret: str, salt: bytes):
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    key = Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(secret.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def _slim_db(path: Path) -> bytes:
    """A compact copy of the database: job descriptions are only kept for jobs
    still waiting in the queue (the rest are history, and the bulk of the file)."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "autopilot.db"
        src, dst = sqlite3.connect(str(path)), sqlite3.connect(str(copy))
        try:
            src.backup(dst)
            dst.execute("UPDATE jobs SET description = '' WHERE status NOT IN ('queued', 'new')")
            dst.commit()
            dst.execute("VACUUM")
        finally:
            src.close()
            dst.close()
        return copy.read_bytes()


def merge_memory(into: Path, other: Path) -> None:
    """Copy what *other* knows that *into* doesn't: who was emailed (latest date wins),
    jobs already acted on, saved form answers and application records. Whichever
    database is kept, no recruiter contacted from the Mac or from GitHub is forgotten."""
    con = sqlite3.connect(str(into))
    try:
        con.execute("ATTACH DATABASE ? AS other", (str(other),))

        def cols(table: str, keep_id: bool = False) -> str:
            """Columns both copies have; autoincrement ids are left for SQLite to assign."""
            mine = [r[1] for r in con.execute(f"PRAGMA main.table_info({table})")]
            theirs = {r[1] for r in con.execute(f"PRAGMA other.table_info({table})")}
            return ", ".join(c for c in mine if c in theirs and (keep_id or c != "id"))

        tables = {r[0] for r in con.execute("SELECT name FROM other.sqlite_master WHERE type = 'table'")}
        if "contacts" in tables:
            con.execute("""INSERT INTO contacts (email, last_contacted, job_id, times)
                           SELECT email, last_contacted, job_id, times FROM other.contacts WHERE true
                           ON CONFLICT(email) DO UPDATE SET
                             last_contacted = max(last_contacted, excluded.last_contacted),
                             times = max(times, excluded.times)""")
        if "answers" in tables:
            c = cols("answers")
            con.execute(f"INSERT OR IGNORE INTO answers ({c}) SELECT {c} FROM other.answers")
        if "pending_questions" in tables:
            c = cols("pending_questions")
            con.execute(f"INSERT OR IGNORE INTO pending_questions ({c}) SELECT {c} FROM other.pending_questions")
        if "jobs" in tables:
            c = cols("jobs", keep_id=True)
            con.execute(f"INSERT OR IGNORE INTO jobs ({c}) SELECT {c} FROM other.jobs")
            con.execute("""UPDATE jobs SET
                             status = (SELECT o.status FROM other.jobs o WHERE o.id = jobs.id),
                             status_reason = (SELECT o.status_reason FROM other.jobs o WHERE o.id = jobs.id)
                           WHERE status NOT IN ('applied', 'manual', 'needs_attention')
                             AND id IN (SELECT id FROM other.jobs
                                        WHERE status IN ('applied', 'manual', 'needs_attention'))""")
        if "applications" in tables:
            c = cols("applications")
            con.execute(f"""INSERT INTO applications ({c}) SELECT {c} FROM other.applications o
                            WHERE NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_id = o.job_id
                                              AND a.channel = o.channel AND a.created_at = o.created_at)""")
        con.commit()
    finally:
        con.close()


def save(root: Path = ROOT, out: Path | None = None, secret: str | None = None) -> list[str]:
    out = out or root / "vault" / "private.bin"
    buf = io.BytesIO()
    packed = []
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name in FILES:
            path = root / name
            if not path.exists():
                continue
            data = _slim_db(path) if name.endswith(".db") else path.read_bytes()
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), int(path.stat().st_mtime), 0o600
            tar.addfile(info, io.BytesIO(data))
            packed.append(name)
    if not packed:
        raise VaultError(f"nothing to save: none of {', '.join(FILES)} exists in {root}")
    salt = os.urandom(16)
    token = _fernet(secret or passphrase(), salt).encrypt(gzip.compress(buf.getvalue(), 9))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_bytes(MAGIC + salt + token)
    tmp.replace(out)
    return packed


def _open(vault: Path, secret: str | None) -> tarfile.TarFile:
    from cryptography.fernet import InvalidToken

    raw = vault.read_bytes()
    if not raw.startswith(MAGIC):
        raise VaultError(f"{vault} is not a vault file")
    salt, token = raw[len(MAGIC):len(MAGIC) + 16], raw[len(MAGIC) + 16:]
    try:
        data = gzip.decompress(_fernet(secret or passphrase(), salt).decrypt(token))
    except InvalidToken as exc:
        raise VaultError(f"wrong {KEY_ENV} for {vault.name} (or the file is damaged)") from exc
    return tarfile.open(fileobj=io.BytesIO(data), mode="r")


def members(vault: Path = VAULT, secret: str | None = None) -> list[tarfile.TarInfo]:
    with _open(vault, secret) as tar:
        return [m for m in tar.getmembers() if m.name in FILES and m.isfile()]


def load(root: Path = ROOT, vault: Path | None = None, secret: str | None = None,
         force: bool = False) -> list[str]:
    """Restore files from the vault. Without *force*, a local file is only
    replaced when the vault's copy is newer; the replaced file is kept as .bak."""
    vault = vault or root / "vault" / "private.bin"
    if not vault.exists():
        raise VaultError(f"no vault at {vault} - run `python3 job_hunt.py vault save` on your Mac first")
    restored = []
    with _open(vault, secret) as tar:
        for member in tar.getmembers():
            if member.name not in FILES or not member.isfile():
                continue          # only the known files, never a path from inside the archive
            target = root / member.name
            data = tar.extractfile(member).read()
            is_db = member.name.endswith(".db")
            if target.exists() and not force and member.mtime <= target.stat().st_mtime + 1:
                if is_db:   # yours is newer: keep it, but learn whom the other side contacted
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        other = Path(tmp_dir) / "vault.db"
                        other.write_bytes(data)
                        merge_memory(target, other)
                    restored.append(f"{member.name} (merged into yours)")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_bytes(data)
            os.chmod(tmp, 0o600)
            if target.exists():
                backup = target.with_name(target.name + ".bak")
                shutil.copy2(target, backup)
                if is_db and not force:
                    merge_memory(tmp, backup)
            tmp.replace(target)
            os.utime(target, (time.time(), member.mtime))
            restored.append(member.name)
    return restored


def auto_load(root: Path = ROOT, log=print) -> list[str]:
    """Before a local run: take the newer database/profile the daily GitHub run
    committed (after `git pull`), so this Mac never re-mails a recruiter the
    cloud run already contacted. Silent when there is no key or no vault."""
    vault = root / "vault" / "private.bin"
    if not vault.exists() or not os.getenv(KEY_ENV):
        return []
    try:
        restored = load(root, vault)
    except VaultError as exc:
        log(f"! vault not loaded: {exc}")
        return []
    if restored:
        log(f"Vault (daily GitHub run's data): {', '.join(restored)}")
    return restored


def status(root: Path = ROOT, log=print) -> int:
    vault = root / "vault" / "private.bin"
    if not vault.exists():
        log(f"No vault yet ({vault}). Create it with: python3 job_hunt.py vault save")
        return 1
    log(f"{vault} ({vault.stat().st_size // 1024} KB)")
    for m in members(vault):
        local = root / m.name
        when = datetime.fromtimestamp(m.mtime).strftime("%d %b %Y %H:%M")
        if not local.exists():
            state = "not on this machine"
        elif m.mtime > local.stat().st_mtime + 1:
            state = "vault is newer"
        elif local.stat().st_mtime > m.mtime + 1:
            state = "yours is newer (vault save to share it)"
        else:
            state = "same"
        log(f"  {m.name:<26} {m.size // 1024:>5} KB  saved {when}  - {state}")
    return 0
