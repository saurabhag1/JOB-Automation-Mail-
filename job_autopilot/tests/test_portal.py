"""Browser flows against local replicas of real application forms (headless Chromium)."""

import json

import pytest

from autopilot import browser
from autopilot.apply import ATTENTION, SUBMITTED
from autopilot.apply.answers import AnswerBook
from autopilot.apply.forms import scan
from autopilot.apply.portal import PortalSession, apply_ats, apply_linkedin
from autopilot.models import APPLY_ASHBY, APPLY_GREENHOUSE, APPLY_LEVER, APPLY_LINKEDIN
from autopilot.tailoring import Package
from conftest import FIXTURES, make_job


@pytest.fixture
def package(tmp_path):
    pdf = tmp_path / "Saurabh_Agrawal_Resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 resume")
    letter = tmp_path / "Cover_Letter.pdf"
    letter.write_bytes(b"%PDF-1.4 letter")
    return Package(job_id="j", folder=tmp_path, resume_pdf=pdf, letter_pdf=letter, cover_note="Dear Hiring Team,",
                   subject="s", signature="Regards", method="rules")


def _session(settings, db, profile, master, auto_submit=True):
    book = AnswerBook(db, profile, master, interactive=False)
    return PortalSession(settings, book, interactive=False, auto_submit=auto_submit, log=lambda *a: None)


@pytest.fixture
def session(settings, db, profile, master):
    sess = _session(settings, db, profile, master)
    yield sess
    browser.shutdown()


def _submitted(sess) -> dict:
    return json.loads(sess.page().locator("#submitted").inner_text())


def test_scan_labels_lever_and_ashby_questions(session):
    page = session.open((FIXTURES / "lever_form.html").as_uri())
    labels = {f.label.rstrip("✱ ") for f in scan(page)}
    assert {"Relevant years of experience", "Do you have experience with Kubernetes?", "Notice Period"} <= labels
    page = session.open((FIXTURES / "ashby_form.html").as_uri())
    fields = {f.label: f for f in scan(page)}
    assert fields["Will you require sponsorship to work in India?"].kind == "buttons"
    assert fields["Will you require sponsorship to work in India?"].required
    assert fields["Gender"].kind == "radio"


def test_greenhouse_form(session, package):
    job = make_job(apply_type=APPLY_GREENHOUSE, apply_url=(FIXTURES / "greenhouse_form.html").as_uri())
    outcome = apply_ats(session, job, package)
    assert outcome.status == SUBMITTED, outcome.detail
    data = _submitted(session)
    assert (data["first_name"], data["last_name"], data["email"]) == ("Saurabh", "Agrawal", "saurabhag012@gmail.com")
    assert data["resume"] == "Saurabh_Agrawal_Resume.pdf" and data["cover_letter"] == "Cover_Letter.pdf"
    assert data["question_101"].startswith("https://www.linkedin.com/in/")
    assert data["question_102"] == "No" and data["question_103"] == "No"
    assert data["gender"] == "3"    # "Decline to self identify"


def test_lever_form(session, package):
    job = make_job(apply_type=APPLY_LEVER, apply_url=(FIXTURES / "lever_form.html").as_uri())
    outcome = apply_ats(session, job, package)
    assert outcome.status == SUBMITTED, outcome.detail
    data = _submitted(session)
    assert data["name"] == "Saurabh Agrawal" and data["org"] == "Example Co"
    assert data["cards[a][field0]"] == "3-5 Years"
    assert data["cards[a][field1]"] == "Yes"
    assert data["cards[a][field2]"] == "Immediate Joiner"
    assert data["cards[b][field0]"] == "Prefer not to disclose"
    assert data["resume"] == "Saurabh_Agrawal_Resume.pdf"


def test_ashby_form(session, package):
    job = make_job(apply_type=APPLY_ASHBY, apply_url=(FIXTURES / "ashby_form.html").as_uri())
    outcome = apply_ats(session, job, package)
    assert outcome.status == SUBMITTED, outcome.detail
    assert _submitted(session) == {"name": "Saurabh Agrawal", "email": "saurabhag012@gmail.com",
                                   "resume": "Saurabh_Agrawal_Resume.pdf", "sponsorship": "No", "gender": "g3"}


def test_linkedin_easy_apply_multi_step(session, package):
    job = make_job(apply_type=APPLY_LINKEDIN, url=(FIXTURES / "linkedin_job.html").as_uri())
    outcome = apply_linkedin(session, job, package)
    assert outcome.status == SUBMITTED, outcome.detail
    data = _submitted(session)
    assert data == {"phone": "+91 7030126522", "resume": "Saurabh_Agrawal_Resume.pdf", "k8s": "3", "auth": "Yes",
                    "sponsorship": "No", "follow": False}


def test_unknown_required_question_hands_over(session, package, db, tmp_path):
    form = tmp_path / "form.html"
    form.write_text("""<form><label for="n">Full name *</label><input id="n" required>
      <fieldset><legend>Are you open to rotational night shifts? *</legend>
      <label><input type="radio" name="s" value="y" required> Yes</label>
      <label><input type="radio" name="s" value="n" required> No</label></fieldset>
      <button type="submit">Submit application</button></form>""")
    job = make_job(apply_type=APPLY_GREENHOUSE, apply_url=form.as_uri())
    outcome = apply_ats(session, job, package)
    assert outcome.status == ATTENTION and "rotational night shifts" in outcome.detail
    assert [p["question"] for p in db.pending()] == ["Are you open to rotational night shifts?"]


def test_review_mode_does_not_submit(settings, db, profile, master, package):
    sess = _session(settings, db, profile, master, auto_submit=False)
    try:
        job = make_job(apply_type=APPLY_ASHBY, apply_url=(FIXTURES / "ashby_form.html").as_uri())
        outcome = apply_ats(sess, job, package)
        assert outcome.status == ATTENTION and "review mode" in outcome.detail
        assert sess.page().locator("#submitted").count() == 0
    finally:
        browser.shutdown()
