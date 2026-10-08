"""Free-tier LLM chain (stand-in HTTP), best-of selection, run folders, Google-search parsing."""

import csv
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from autopilot import browser
from autopilot.apply import SUBMITTED, Outcome
from autopilot.discovery import google_search
from autopilot.runlog import RunFolder
from autopilot.tailoring import Tailor, rules
from autopilot.tailoring.free_llm import FreeLLMChain
from autopilot.tailoring.llm import LLMError
from conftest import make_job


def _draft(master, invent: bool) -> dict:
    roles = [{"role_id": r["id"], "bullets": [{"source_id": b["id"], "text": f"{b['label']}: {b['text']}" if b["label"]
                                               else b["text"]} for b in r["bullets"]]} for r in master["experience"]]
    if invent:
        roles[1]["bullets"][3]["text"] = "Standardized Kubernetes deployments using Helm and Pulumi, cutting toil by 70%."
    return {"summary": "DevOps & Cloud/SRE Engineer with 3+ years of experience running AWS EKS, Terraform and "
                       "Jenkins CI/CD with Prometheus and Grafana monitoring.",
            "experience": roles,
            "cover_note": ("Dear Hiring Team at Acme,\n\nI am excited to apply for the DevOps Engineer role at Acme. "
                           "With 3+ years of experience I have built CI/CD pipelines with GitHub Actions and Jenkins "
                           "and run AWS EKS clusters with Terraform, reducing deployment cycles by 50%.\n\n"
                           "At Immersive I strengthened CI/CD pipelines for 40% faster deployments and centralized "
                           "Prometheus and Grafana monitoring, mitigating MTTR by 35%.\n\nI'm an immediate joiner "
                           "and would welcome the opportunity to talk. Please find my resume attached."),
            "email_subject": "Application for DevOps Engineer - Saurabh Agrawal",
            "matched_requirements": [], "missing_requirements": []}


class FakeHTTP:
    """requests.post stand-in: Gemini's first model is overloaded, the rest answer."""

    def __init__(self, master, gemini_invents=True, groq_status=200):
        self.master, self.gemini_invents, self.groq_status, self.calls = master, gemini_invents, groq_status, []

    def __call__(self, url, headers=None, json=None, timeout=None):  # noqa: A002
        self.calls.append(url)
        if "generativelanguage" in url:
            if "gemini-3.8-flash" in url:
                return SimpleNamespace(status_code=503, ok=False, text="high demand", json=lambda: {})
            body = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
                {"text": __import__("json").dumps(_draft(self.master, self.gemini_invents))}]}}],
                "usageMetadata": {"promptTokenCount": 5000, "candidatesTokenCount": 1500}}
            return SimpleNamespace(status_code=200, ok=True, text="", json=lambda: body)
        if self.groq_status != 200:
            return SimpleNamespace(status_code=self.groq_status, ok=False, text="nope", json=lambda: {})
        body = {"choices": [{"finish_reason": "stop", "message": {"content": __import__("json").dumps(
            _draft(self.master, False))}}], "usage": {"prompt_tokens": 4000, "completion_tokens": 1200}}
        return SimpleNamespace(status_code=200, ok=True, text="", json=lambda: body)


@pytest.fixture
def free_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("autopilot.tailoring.llm.credentials_present", lambda: False)
    monkeypatch.setattr("autopilot.tailoring.credentials_present", lambda: False)
    for k in ("GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.setenv(k, "test")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def test_chain_falls_through_busy_models(master, profile, settings, free_env, monkeypatch):
    fake = FakeHTTP(master)
    monkeypatch.setattr("autopilot.tailoring.free_llm.requests.post", fake)
    chain = FreeLLMChain(settings, master, profile, log=lambda *a: None)
    labels = [label for label, _ in chain.drafts(make_job(), True)]
    assert labels == ["gemini:gemini-3.5-flash", "groq:openai/gpt-oss-120b"]
    assert chain.usage["calls"] == 2 and chain.usage["input"] == 9000


def test_rejected_key_turns_provider_off(master, profile, settings, free_env, monkeypatch):
    monkeypatch.setattr("autopilot.tailoring.free_llm.requests.post", FakeHTTP(master, groq_status=401))
    chain = FreeLLMChain(settings, master, profile, log=lambda *a: None)
    assert [label for label, _ in chain.drafts(make_job(), True)] == ["gemini:gemini-3.5-flash"]
    assert "groq" in chain.dead


def test_all_providers_busy_is_a_per_job_error(master, profile, settings, free_env, monkeypatch):
    busy = lambda *a, **k: SimpleNamespace(status_code=429, ok=False, text="", json=lambda: {})  # noqa: E731
    monkeypatch.setattr("autopilot.tailoring.free_llm.requests.post", busy)
    chain = FreeLLMChain(settings, master, profile, log=lambda *a: None)
    with pytest.raises(LLMError):
        chain.drafts(make_job(), True)


def test_best_of_picks_the_draft_the_guard_trusts(master, profile, settings, free_env, monkeypatch, tmp_path):
    monkeypatch.setattr("autopilot.tailoring.free_llm.requests.post", FakeHTTP(master, gemini_invents=True))
    try:
        pkg = Tailor(master, profile, settings, tmp_path, log=lambda *a: None, dated=False).build(make_job(company="Acme"))
    finally:
        browser.shutdown()
    assert pkg.method == "groq:openai/gpt-oss-120b"          # Gemini's draft invented Pulumi / 70%
    assert any(n.startswith("drafts compared") for n in pkg.notes)
    html = re.sub(r"<[^>]+>", "", (pkg.folder / "Saurabh_Agrawal_Resume.html").read_text())
    assert "Pulumi" not in html and "70%" not in html
    assert pkg.folder.parent == tmp_path / "applications"   # flat inside the run folder


def test_template_note_uses_your_email(master, profile):
    template = Path(__file__).resolve().parents[2] / "bulk_mail_sender" / "template.html"
    if not template.exists():
        pytest.skip("template not available")
    paras = rules.template_paragraphs(template.read_text())
    job = make_job(company="Acme", title="AWS DevOps Engineer")
    note = rules.template_note("\n\n".join(paras), master, profile, job, {"aws", "kubernetes", "terraform", "cicd"},
                               rules.jd_weights(job))
    assert note.startswith("Dear Hiring Team at Acme,")
    assert "the AWS DevOps Engineer role at Acme" in note
    assert "immediate joiner" in note and "joinner" not in note and "Regards" not in note


def test_run_folder_contents(tmp_path):
    run = RunFolder(tmp_path)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-(AM|PM)_IST", run.dir.name)
    job = make_job(hr_email="megha@quicksort.co.in", company="Quicksort", tier=2)
    job.chance, job.score = 0.81, 0.9
    run.accepted, run.rejected = [job], [make_job(url="https://x/2").reject("asks for 6+ years")]
    pkg = SimpleNamespace(subject="Application for DevOps Engineer", resume_pdf=tmp_path / "r.pdf", method="rules")
    run.record(job, "email", Outcome(SUBMITTED, "emailed", recipient="megha@quicksort.co.in"), pkg)
    md = run.write(__import__("collections").Counter({"submitted": 1}), __import__("collections").Counter({"naukri": 3}))
    assert md.name == f"{run.dir.name}.md" and (run.dir / f"{run.dir.name}.xlsx").exists()
    hr = list(csv.DictReader(open(run.dir / "hr_emails.csv")))
    assert hr[0]["HR email"] == "megha@quicksort.co.in" and hr[0]["Status"].startswith("submitted")
    sent = list(csv.DictReader(open(run.dir / "sent.csv")))
    assert sent[0]["Sent to / where"] == "megha@quicksort.co.in"
    assert "megha@quicksort.co.in" in md.read_text() and "asks for 6+ years" in open(run.dir / "rejected.csv").read()


def test_google_plan_and_parsing(settings):
    queries = google_search.plan_queries(settings)
    assert len(queries) == 12
    groups = [g for g, _, _ in queries]
    assert groups.count("recruiter_posts") == 6 and "india" in groups and "gulf_asia" in groups
    assert all('"' in q and "site:" in q for _, _, q in queries)
    post = {"link": "https://www.linkedin.com/posts/megha-hr_hiring-devops-activity-1234567890",
            "title": "Megha S. on LinkedIn: We're hiring a DevOps Engineer!", "date": "2 days ago",
            "snippet": "Experience: 2-4 years. AWS, Kubernetes, Terraform. Share your resume at megha@quicksort.co.in"}
    job = google_search._to_job("recruiter_posts", "Pune", "DevOps Engineer", post, "")
    assert job.hr_email == "megha@quicksort.co.in" and job.title == "DevOps Engineer" and job.company == "Quicksort"
    assert job.apply_type == "email" and job.date_trusted and job.country == "India"
    listing = {"link": "https://www.naukri.com/devops-engineer-jobs-in-pune", "title": "DevOps Jobs in Pune",
               "snippet": "1,234 DevOps jobs"}
    assert google_search._to_job("india", "Pune", "DevOps Engineer", listing, "") is None
