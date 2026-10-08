"""The pieces the daily scheduled run depends on: the APPLY_MANUALLY folder, the
employer-form finder and the encrypted vault (with its no-one-mailed-twice merge)."""

import sqlite3
from collections import Counter
from types import SimpleNamespace

import pytest
from conftest import make_job

from autopilot import vault
from autopilot.db import Database
from autopilot.discovery import resolve
from autopilot.models import APPLY_ASHBY, APPLY_GREENHOUSE, APPLY_LINKEDIN_OFFSITE
from autopilot.runlog import MANUAL_DIR, RunFolder, prune_old_runs

KEY = "a-test-passphrase-123456"


# --------------------------------------------------------------------------- APPLY_MANUALLY
def _package(folder):
    folder.mkdir(parents=True)
    (folder / "Saurabh_Agrawal_Resume.pdf").write_bytes(b"%PDF-1.4 resume")
    (folder / "Cover_Letter.pdf").write_bytes(b"%PDF-1.4 letter")
    (folder / "email.txt").write_text("Subject: x\n\nHello", encoding="utf-8")
    return SimpleNamespace(folder=folder, resume_pdf=folder / "Saurabh_Agrawal_Resume.pdf",
                           letter_pdf=folder / "Cover_Letter.pdf", cover_note="Hello, I am applying.")


def test_manual_jobs_get_their_own_folder(tmp_path):
    run = RunFolder(tmp_path)
    job = make_job(title="DevOps Engineer", company="Wipro", url="https://www.linkedin.com/jobs/view/1")
    pkg = _package(run.dir / "applications" / "wipro-devops-engineer-abc123")
    run.add_manual(job, "LinkedIn 'apply on company site'", pkg)
    run.add_manual(make_job(company="Acme", url="https://acme.example/jobs/2"), "employer's own careers site")
    run.write(Counter(), Counter())

    moved = run.dir / MANUAL_DIR / "01-wipro-devops-engineer-abc123"
    assert (moved / "Saurabh_Agrawal_Resume.pdf").exists() and (moved / "APPLY.md").exists()
    assert not (run.dir / "applications" / "wipro-devops-engineer-abc123").exists()
    assert pkg.resume_pdf == moved / "Saurabh_Agrawal_Resume.pdf"          # later records point to the new place
    note = (moved / "APPLY.md").read_text(encoding="utf-8")
    assert "https://www.linkedin.com/jobs/view/1" in note and "Hello, I am applying." in note
    readme = (run.dir / MANUAL_DIR / "README.md").read_text(encoding="utf-8")
    assert "(01-wipro-devops-engineer-abc123/)" in readme and "link only" in readme
    summary = (run.dir / f"{run.stamp}.md").read_text(encoding="utf-8")
    assert f"Apply by hand (2)" in summary and f"({MANUAL_DIR}/01-wipro-devops-engineer-abc123/)" in summary
    assert (run.dir / "apply_manually.csv").read_text(encoding="utf-8").count("\n") == 3


def test_old_runs_keep_reports_but_lose_bulky_files(tmp_path):
    old, new = tmp_path / "2020-01-01_10-00-AM_IST", tmp_path / "2999-01-01_10-00-AM_IST"
    for run in (old, new):
        (run / MANUAL_DIR / "01-x").mkdir(parents=True)
        (run / MANUAL_DIR / "01-x" / "r.pdf").write_bytes(b"pdf")
        (run / "report.md").write_text("kept", encoding="utf-8")
    assert prune_old_runs(tmp_path, 30, log=lambda *a: None) == 1
    assert not (old / MANUAL_DIR / "01-x" / "r.pdf").exists() and (old / "report.md").exists()
    assert (new / MANUAL_DIR / "01-x" / "r.pdf").exists()


# --------------------------------------------------------------------------- employer forms
class FakeResponse:
    def __init__(self, url, text="", payload=None, status=200):
        self.url, self.text, self._payload, self.status_code = url, text, payload, status
        self.ok = status == 200

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, timeout=None):
        self.calls.append(url)
        for prefix, resp in self.routes.items():
            if url.startswith(prefix):
                return resp
        return FakeResponse(url, status=404)


def test_career_page_embedding_greenhouse_is_found():
    url = "https://careers.example.com/jobs?gh_jid=4567"
    page = '<script src="https://boards.greenhouse.io/embed/job_board/js?for=examplecorp"></script>'
    sess = FakeSession({url: FakeResponse(url, page)})
    kind, form, _ = resolve.page_route(url, sess)
    assert kind == APPLY_GREENHOUSE and form.endswith("for=examplecorp&token=4567")


def test_page_listing_many_forms_is_ambiguous_and_aggregators_are_not_fetched():
    url = "https://careers.example.com/openings"
    page = ("https://jobs.lever.co/example/11111111-2222-3333-4444-555555555555 "
            "https://jobs.lever.co/example/66666666-7777-8888-9999-000000000000")
    assert resolve.page_route(url, FakeSession({url: FakeResponse(url, page)})) is None
    sess = FakeSession({})
    assert resolve.page_route("https://in.indeed.com/viewjob?jk=1", sess) is None and not sess.calls


def test_company_board_match_needs_title_and_place():
    board = {"jobs": [
        {"title": "Site Reliability Engineer", "location": "Remote - US", "isListed": True,
         "applyUrl": "https://jobs.ashbyhq.com/acme/aaa/application"},
        {"title": "Site Reliability Engineer", "location": "Bengaluru, India", "isListed": True,
         "applyUrl": "https://jobs.ashbyhq.com/acme/bbb/application"},
    ]}
    sess = FakeSession({"https://api.ashbyhq.com/posting-api/job-board/acme": FakeResponse("", payload=board)})
    index = resolve.BoardIndex(sess)
    job = make_job(title="Site Reliability Engineer", company="Acme Technologies", location="Bengaluru, Karnataka, India",
                   apply_type=APPLY_LINKEDIN_OFFSITE)
    kind, form, _ = index.route(job)
    assert kind == APPLY_ASHBY and form.endswith("/bbb/application")      # the Indian posting, not the US one
    assert index.route(make_job(title="Data Analyst", company="Acme", location="Bengaluru, India")) is None
    assert index.route(make_job(title="Site Reliability Engineer", company="Acme", location="Berlin, Germany")) is None


def test_resolve_routes_updates_job_and_database(settings, db, monkeypatch):
    job = make_job(source="linkedin", url="https://www.linkedin.com/jobs/view/9", apply_type=APPLY_LINKEDIN_OFFSITE,
                   company="Okta")
    db.upsert_job(job, status="queued")
    monkeypatch.setattr(resolve.BoardIndex, "route",
                        lambda self, j: (APPLY_GREENHOUSE, "https://job-boards.greenhouse.io/embed/job_app?for=okta&token=1",
                                         "same job on Okta's greenhouse board"))
    assert resolve.resolve_routes([job], settings, db, log=lambda *a: None) == 1
    row = db.job_row(job.id)
    assert row["apply_type"] == APPLY_GREENHOUSE and "token=1" in row["apply_url"]


# --------------------------------------------------------------------------- vault
def _private_files(root, email="hr@example.com", when="2026-10-01T00:00:00+00:00"):
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "profile.yaml").write_text("name: Saurabh\n", encoding="utf-8")
    (root / "data" / "master_resume.json").write_text("{}", encoding="utf-8")
    database = Database(root / "data" / "autopilot.db")
    database.conn.execute("INSERT INTO contacts (email, last_contacted, job_id, times) VALUES (?, ?, 'j', 1)",
                          (email, when))
    database.conn.commit()
    database.close()


def _contacts(path):
    con = sqlite3.connect(str(path))
    try:
        return dict(con.execute("SELECT email, last_contacted FROM contacts").fetchall())
    finally:
        con.close()


def test_vault_round_trip_and_wrong_key(tmp_path):
    mac, cloud = tmp_path / "mac", tmp_path / "cloud"
    _private_files(mac)
    assert vault.save(mac, secret=KEY) == list(vault.FILES)
    blob = (mac / "vault" / "private.bin").read_bytes()
    assert b"Saurabh" not in blob and b"hr@example.com" not in blob          # nothing readable in the repo
    cloud.mkdir()
    (cloud / "vault").mkdir()
    (cloud / "vault" / "private.bin").write_bytes(blob)
    assert sorted(vault.load(cloud, secret=KEY)) == sorted(vault.FILES)
    assert (cloud / "profile.yaml").read_text(encoding="utf-8") == "name: Saurabh\n"
    assert _contacts(cloud / "data" / "autopilot.db") == {"hr@example.com": "2026-10-01T00:00:00+00:00"}
    with pytest.raises(vault.VaultError, match="wrong"):
        vault.load(cloud, secret="another-passphrase-0000", force=True)


def test_vault_never_forgets_a_contact(tmp_path):
    """Mac and cloud both mailed people: whichever database wins, both contacts survive."""
    cloud, mac = tmp_path / "cloud", tmp_path / "mac"
    _private_files(cloud, "cloud-hr@example.com")
    vault.save(cloud, secret=KEY)
    _private_files(mac, "mac-hr@example.com")
    (mac / "vault").mkdir()
    (mac / "vault" / "private.bin").write_bytes((cloud / "vault" / "private.bin").read_bytes())

    # Mac's database is newer: it is kept, and the cloud's contact is merged in.
    restored = vault.load(mac, secret=KEY)
    assert "data/autopilot.db (merged into yours)" in restored
    assert set(_contacts(mac / "data" / "autopilot.db")) == {"cloud-hr@example.com", "mac-hr@example.com"}

    # Vault's database is newer: it replaces the Mac's, which is kept as .bak and merged in.
    import os

    _private_files(mac, "only-on-mac@example.com")
    os.utime(mac / "data" / "autopilot.db", (0, 0))
    assert "data/autopilot.db" in vault.load(mac, secret=KEY)
    assert (mac / "data" / "autopilot.db.bak").exists()
    assert set(_contacts(mac / "data" / "autopilot.db")) >= {"cloud-hr@example.com", "only-on-mac@example.com"}


def test_vault_ignores_unknown_paths(tmp_path):
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("../evil.txt")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"bad"))
    import gzip
    import os

    salt = os.urandom(16)
    token = vault._fernet(KEY, salt).encrypt(gzip.compress(buf.getvalue()))
    (tmp_path / "vault").mkdir()
    (tmp_path / "vault" / "private.bin").write_bytes(vault.MAGIC + salt + token)
    assert vault.load(tmp_path, secret=KEY) == []
    assert not (tmp_path.parent / "evil.txt").exists()
