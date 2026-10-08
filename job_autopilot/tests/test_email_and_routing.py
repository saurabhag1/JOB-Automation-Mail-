import email
import time
from pathlib import Path

import pytest

from autopilot.apply import DRY_RUN, SUBMITTED, choose_channel, existing_pipeline_addresses
from autopilot.apply.email_channel import Mailer
from autopilot.models import APPLY_GREENHOUSE, APPLY_LINKEDIN
from autopilot.tailoring import Package
from conftest import make_job


def _package(tmp_path: Path) -> Package:
    pdf = tmp_path / "Saurabh_Agrawal_Resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    return Package(job_id="j1", folder=tmp_path, resume_pdf=pdf, letter_pdf=None,
                   cover_note="Dear Hiring Team,\n\nI'm applying for the DevOps role.", subject="Application for DevOps",
                   signature="Regards,\nSaurabh Agrawal", method="rules")


def test_routing(db, settings):
    channels = ["email", "ats", "linkedin"]
    assert choose_channel(make_job(hr_email="megha@quicksort.co.in"), channels, db, settings, set())[0] == "email"
    assert choose_channel(make_job(hr_email="megha@quicksort.co.in"), channels, db, settings,
                          {"megha@quicksort.co.in"}) == ("skip", "already mailed by your GitHub Actions pipeline")
    gh = make_job(hr_email="megha@quicksort.co.in", apply_type=APPLY_GREENHOUSE)
    assert choose_channel(gh, channels, db, settings, {"megha@quicksort.co.in"})[0] == "ats"
    assert choose_channel(make_job(apply_type=APPLY_LINKEDIN), ["email", "ats"], db, settings, set())[0] == "manual"
    db.record_contact("old@acme.io", "x")
    assert choose_channel(make_job(hr_email="old@acme.io"), channels, db, settings, set())[0] == "skip"


def test_existing_pipeline_addresses_are_read(settings):
    seen = existing_pipeline_addresses(settings)
    assert len(seen) > 0 and all("@" in a for a in seen)


def test_message_shape(profile, tmp_path):
    msg = Mailer(profile, dry_run=True).build(make_job(hr_email="hr@acme.io"), _package(tmp_path))
    assert msg["To"] == "hr@acme.io" and msg["From"].endswith("<saurabhag012@gmail.com>")
    parts = [p.get_content_type() for p in msg.walk()]
    assert {"text/plain", "text/html", "application/pdf"} <= set(parts)


def test_dry_run_saves_eml_and_sends_nothing(profile, tmp_path):
    outcome = Mailer(profile, dry_run=True).send(make_job(hr_email="hr@acme.io"), _package(tmp_path))
    assert outcome.status == DRY_RUN and (tmp_path / "email.eml").exists()


def test_real_smtp_round_trip(profile, tmp_path, monkeypatch):
    aiosmtpd = pytest.importorskip("aiosmtpd.controller")
    received = []

    class Handler:
        async def handle_DATA(self, server, session, envelope):
            received.append((envelope.rcpt_tos, envelope.content))
            return "250 OK"

    controller = aiosmtpd.Controller(Handler(), hostname="127.0.0.1", port=8025, auth_require_tls=False,
                                     auth_exclude_mechanism=[], authenticator=lambda *a: __import__(
                                         "aiosmtpd.smtp", fromlist=["AuthResult"]).AuthResult(success=True))
    controller.start()
    try:
        for key, value in {"SMTP_HOST": "127.0.0.1", "SMTP_PORT": "8025", "SMTP_STARTTLS": "0",
                           "EMAIL_ADDRESS": "saurabhag012@gmail.com", "EMAIL_PASSWORD": "x"}.items():
            monkeypatch.setenv(key, value)
        mailer = Mailer(profile, dry_run=False, log=lambda *a: None)
        outcome = mailer.send(make_job(hr_email="hr@acme.io"), _package(tmp_path))
        mailer.close()
        time.sleep(0.2)
    finally:
        controller.stop()
    assert outcome.status == SUBMITTED
    rcpts, content = received[0]
    assert rcpts == ["hr@acme.io"]
    parsed = email.message_from_bytes(content)
    attachments = [p.get_filename() for p in parsed.walk() if p.get_content_disposition() == "attachment"]
    assert attachments == ["Saurabh_Agrawal_Resume.pdf"]
