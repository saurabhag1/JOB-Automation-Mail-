"""The pieces the daily scheduled run depends on: the APPLY_MANUALLY folder and the
employer-form finder."""

from collections import Counter
from types import SimpleNamespace

from conftest import make_job

from autopilot.discovery import resolve
from autopilot.models import APPLY_ASHBY, APPLY_GREENHOUSE, APPLY_LINKEDIN_OFFSITE
from autopilot.runlog import MANUAL_DIR, RunFolder, prune_old_runs


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
