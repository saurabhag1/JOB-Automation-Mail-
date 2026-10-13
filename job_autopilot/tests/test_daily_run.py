"""The pieces the daily scheduled run depends on: the APPLY_MANUALLY folder and the
employer-form finder."""

from collections import Counter
from datetime import date, timedelta
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


# --------------------------------------------------------------------------- AI search rounds
from autopilot.discovery import ai_search  # noqa: E402


class FakeLLM:
    """Answers expand_queries / read_posts prompts with canned JSON."""

    def __init__(self, posts=None):
        self.prompts, self.posts = [], posts or []
        self.calls = Counter()

    def __bool__(self):
        return True

    def json(self, prompt):
        self.prompts.append(prompt)
        if "Write" in prompt and "search phrases" in prompt:
            n = len([p for p in self.prompts if "search phrases" in p])
            return {"queries": [f"SRE hiring Pune immediate joiners round{n}", "site:linkedin.com bad", '"x"',
                                "Cloud Engineer 3+ years Hyderabad share CV"]}
        return {"posts": self.posts}


def test_ai_search_rounds_stop_when_dry_and_never_trust_llm_emails(settings, monkeypatch):
    settings.data["sources"]["ai_search"] = {"enabled": True, "web_searches": 6, "max_rounds": 5}
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setattr(ai_search.gs, "available_providers", lambda: [])
    counter = {"n": 0}

    def fake_web(query, settings, sess):
        n = counter["n"]
        counter["n"] += 1
        if n >= 2:                      # round 1 runs 2 searches; from round 2 on nothing new turns up
            return []
        return [{"link": f"https://www.linkedin.com/posts/hr-{n}_hiring-activity-{n}",
                 "title": "Hiring DevOps Engineer | Pune", "date": (date.today() - timedelta(days=1)).isoformat(),
                 "snippet": f"We are hiring a DevOps Engineer, 2-4 years, AWS and Kubernetes. "
                            f"Share your resume at hr{n}@acme{n}.com"}]

    monkeypatch.setattr(ai_search, "web_search", fake_web)
    monkeypatch.setattr(ai_search.gs, "_page_text", lambda sess, url: "")
    llm = FakeLLM(posts=[{"i": 0, "title": "DevOps Engineer", "company": "Invented Corp",
                          "experience": "2-4 years", "hiring": True}])
    monkeypatch.setattr("autopilot.tailoring.free_llm.QuickLLM", lambda settings, log: llm)

    jobs = ai_search.collect(settings, log=lambda *a: None)
    assert sorted(j.hr_email for j in jobs) == ["hr0@acme0.com", "hr1@acme1.com"]
    assert all(j.company != "Invented Corp" for j in jobs)          # not in the post text -> ignored
    assert jobs[0].experience_text == "2-4 years"                    # in the post text -> used
    phrase_prompts = [p for p in llm.prompts if "search phrases" in p]
    assert len(phrase_prompts) == 2                                  # rounds 2 and 3 found nothing -> stopped

    # With a target of 2 HR emails, round 1 already reaches it: no follow-up searches at all.
    counter["n"] = 0
    llm.prompts.clear()
    settings.data["sources"]["ai_search"]["min_hr_emails"] = 2
    ai_search.collect(settings, log=lambda *a: None)
    assert not [p for p in llm.prompts if "search phrases" in p]


def test_expand_queries_cleans_llm_output():
    out = ai_search.expand_queries(FakeLLM(), ["DevOps Engineer"], ["Pune"], 3,
                                   used=["Cloud Engineer 3+ years Hyderabad share CV"], examples=[], n=5)
    assert out == ["SRE hiring Pune immediate joiners round1", "linkedin.com bad"] or \
        out == ["SRE hiring Pune immediate joiners round1"]
    assert all("site:" not in q and '"' not in q for q in out)


def test_not_hiring_posts_are_flagged():
    job = make_job(source="ai-web:linkedin.com", hr_email="info@training.example",
                   description="New batches starting this week, limited seats. DevOps course. info@training.example")
    ai_search.read_posts(FakeLLM(posts=[{"i": 0, "title": "DevOps course", "hiring": False}]), [job], log=lambda *a: None)
    assert job.title.endswith("[not a hiring post]")


def test_not_sent_reasons_are_counted():
    hr = [{"Status": s} for s in ("submitted 02:30 PM IST", "not sent: emailed 0 days ago", "not sent: emailed 3 days ago",
                                  "not sent: already mailed by your GitHub Actions pipeline",
                                  "not contacted: title not a DevOps/Cloud role",
                                  "not contacted: asks for 7+ years (you have 3)", "queued (over this run's cap)")]
    assert RunFolder.not_sent_reasons(hr) == Counter({
        "already emailed by an earlier run (60-day gap)": 2, "already mailed by your GitHub Actions pipeline": 1,
        "title not a DevOps/Cloud role": 1, "asks for more years than you have": 1, "queued": 1})


# --------------------------------------------------------------------------- search hygiene
def test_post_location_beats_the_searched_city():
    from autopilot.discovery import google_search as gs

    def loc(title, snippet):
        job = gs._to_job("recruiter_posts", "Gurugram", "DevOps Engineer",
                         {"link": "https://www.linkedin.com/posts/x-activity-7511334144832897024-a",
                          "title": title, "snippet": snippet}, "")
        return job.location, job.country

    assert loc("Kubernetes Engineer", "12 month contract in Philadelphia, PA") == ("United States", "United States")
    assert loc("Senior DevOps Engineer", "Hybrid role in Bangkok") == ("Thailand", "Thailand")
    assert loc("DevOps Engineer", "Gurugram, India. Our clients are in the US and UK") == ("Gurugram", "India")
    assert loc("DevOps Engineer", "share your resume") == ("Gurugram", "India")       # no place named: the search's


def test_dead_search_key_is_skipped_for_the_run(monkeypatch):
    import requests

    from autopilot.discovery import google_search as gs

    calls = []

    def dead(sess, q, key):
        calls.append("dead")
        resp = requests.Response()
        resp.status_code = 403
        raise requests.HTTPError(response=resp)

    def ok(sess, q, key):
        calls.append("ok")
        return [{"link": "https://x"}]

    monkeypatch.setattr(gs, "SEARCH", {"scrapingdog": dead, "serper": ok})
    monkeypatch.setattr(gs, "_DEAD", set())
    monkeypatch.setenv("SCRAPINGDOG_KEY", "k")
    monkeypatch.setenv("SERPER_KEY", "k")
    assert gs.search_any(None, "q", ["scrapingdog", "serper"], 0) == [{"link": "https://x"}]
    assert gs.search_any(None, "q", ["scrapingdog", "serper"], 0) == [{"link": "https://x"}]
    assert calls == ["dead", "ok", "ok"]                 # the dead key was tried once, then skipped


def test_web_search_stops_after_no_credit(settings, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(ai_search, "_WEB_OFF", [])
    posts = []
    sess = SimpleNamespace(post=lambda *a, **k: posts.append(1) or SimpleNamespace(status_code=402))
    import pytest

    with pytest.raises(ai_search.WebSearchUnavailable, match="no credit"):
        ai_search.web_search("q", settings, sess)
    assert ai_search.web_search("q", settings, sess) == [] and posts == [1]


def test_display_title():
    from autopilot.tailoring.email_template import display_title

    assert display_title("AZURE CLOUD ENGINEER") == "Azure Cloud Engineer"
    assert display_title("DEVOPS ENGINEER L4") == "DevOps Engineer L4"
    assert display_title("AWS DevOps Engineer") == "AWS DevOps Engineer"
