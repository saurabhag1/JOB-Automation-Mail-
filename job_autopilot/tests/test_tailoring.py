from pypdf import PdfReader

from autopilot import browser
from autopilot.resume.parser import resume_text
from autopilot.tailoring import Tailor, guard, rules
from autopilot.text import find_terms, implied
from conftest import make_job


def test_rules_put_relevant_bullets_first(master):
    job = make_job(title="Observability / SRE Engineer",
                   description="Own Prometheus, Grafana, Loki and Tempo observability. Reduce MTTR. SRE practices.")
    weights = rules.jd_weights(job)
    tailored = rules.tailor_resume(master, job, weights, max_projects=4)
    assert tailored["experience"][0]["bullets"][0]["label"] == "Intelligent Observability (SRE)"
    assert len(tailored["projects"]) == 4
    # reordering only: the same bullets are all still there
    assert sorted(b["id"] for b in tailored["experience"][0]["bullets"]) == \
        sorted(b["id"] for b in master["experience"][0]["bullets"])


def test_rule_texts_pass_the_guard(master, profile):
    job = make_job(company="Okta", title="Site Reliability Engineer",
                   description="Kubernetes, Terraform, AWS, Go, Datadog, PagerDuty on-call, 2-4 years.")
    keys = implied(find_terms(resume_text(master)))
    weights = rules.jd_weights(job)
    summary = rules.summary(master, profile, keys, weights)
    note = rules.cover_note(master, profile, job, keys, weights)
    everything = resume_text(master)
    assert guard.problems(summary, everything, everything, everything, 3) == []
    assert guard.problems(note, everything, everything, everything + "\nOkta Site Reliability Engineer", 3) == []
    assert "Datadog" not in note and "PagerDuty" not in note      # requirements the resume lacks are not claimed
    assert note.startswith("Dear Hiring Team at Okta,") and "join immediately" in note


def test_package_renders_two_page_pdf(master, profile, settings, tmp_path):
    settings.data["tailoring"]["llm"] = "none"
    job = make_job(company="Acme Cloud", title="Platform Engineer")
    try:
        pkg = Tailor(master, profile, settings, tmp_path, log=lambda *a: None).build(job)
    finally:
        browser.shutdown()
    assert pkg.method == "rules" and pkg.pages == 2
    assert pkg.resume_pdf.name == "Saurabh_Agrawal_Resume.pdf"
    text = "".join(p.extract_text() for p in PdfReader(str(pkg.resume_pdf)).pages)
    assert "Immersive Pvt Ltd" in text and "Saurabh Agrawal" in text
    assert (pkg.folder / "package.json").exists() and pkg.letter_pdf.exists()
    assert pkg.subject == "Application for Platform Engineer | Saurabh Agrawal | 3+ yrs DevOps & Cloud"
