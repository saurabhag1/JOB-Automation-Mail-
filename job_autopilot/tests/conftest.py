import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autopilot.config import Profile, load_settings  # noqa: E402
from autopilot.db import Database  # noqa: E402
from autopilot.models import Job  # noqa: E402

RESUME_PDF = ROOT.parent / "bulk_mail_sender" / "Saurabh_Agrawal_2026.pdf"
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture(scope="session")
def master():
    if not RESUME_PDF.exists():
        pytest.skip("master resume PDF not available")
    from autopilot.resume.parser import build_master

    return build_master(RESUME_PDF)


@pytest.fixture
def settings(tmp_path):
    s = load_settings()
    s.data.setdefault("browser", {}).update(channel="chromium", headless=True, slow_mo_ms=0,
                                            profile_dir=str(tmp_path / "browser-profile"))
    s.data["paths"] = {"db": str(tmp_path / "t.db"), "output": str(tmp_path / "out")}
    return s


@pytest.fixture
def profile():
    return Profile({
        "name": "Saurabh Agrawal", "first_name": "Saurabh", "last_name": "Agrawal",
        "email": "saurabhag012@gmail.com", "phone": "+91 7030126522", "city": "Pune", "state": "Maharashtra",
        "country": "India", "linkedin": "https://www.linkedin.com/in/saurabh-agrawal-a11392277/",
        "github": "https://github.com/saurabhag1", "portfolio": "https://saurabh-portfolio-three.vercel.app/",
        "current_title": "DevOps & Cloud Engineer", "current_company": "Example Co", "years_experience": 3,
        "notice_period": "Immediate", "notice_period_days": 0, "current_ctc": "8 LPA", "expected_ctc": "12 LPA",
        "expected_salary_usd": "30000", "willing_to_relocate": True, "open_to": ["remote", "hybrid", "onsite"],
        "authorized_countries": ["India"], "education": "B.Tech CSE (2023)", "graduation_year": 2023,
        "skill_years": {}, "eeo": {"gender": "Prefer not to say", "ethnicity": "Prefer not to say",
                                   "veteran": "Prefer not to say", "disability": "Prefer not to say"},
        "how_did_you_hear": "Job board",
    })


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "t.db")
    yield database
    database.close()


def make_job(**kw) -> Job:
    base = dict(source="linkedin", title="DevOps Engineer", company="Acme", url="https://example.com/job/1",
                location="Pune, Maharashtra, India", country="India",
                description=("We need a DevOps Engineer with 2-4 years of experience in AWS, Kubernetes, Terraform, "
                             "Docker, Jenkins and Prometheus/Grafana monitoring. CI/CD pipelines on EKS."),
                date_posted=datetime.now(timezone.utc) - timedelta(days=1), date_trusted=True)
    base.update(kw)
    return Job(**base)
