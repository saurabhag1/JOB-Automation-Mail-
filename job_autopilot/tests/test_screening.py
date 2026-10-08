from datetime import datetime, timedelta, timezone

from autopilot.models import APPLY_GREENHOUSE, APPLY_LINKEDIN
from autopilot.screening import (dedupe, eligibility_reason, experience_reason, freshness_reason, genuineness,
                                 screen, tier_of, title_reason)
from conftest import make_job

NOW = datetime.now(timezone.utc)


def test_titles(settings):
    assert title_reason("Site Reliability Engineer (SRE)", settings) == ""
    assert title_reason("Staff DevOps Engineer", settings) == "title contains 'staff'"
    assert title_reason("Internal Tools Platform Engineer", settings) == ""      # 'intern ' != 'internal'
    assert title_reason("DevOps Intern", settings).startswith("title contains 'intern")
    assert title_reason("Data Engineer (AWS)", settings) == "title contains 'data engineer'"
    assert title_reason("Accountant", settings) == "title not a DevOps/Cloud role"


def test_freshness(settings):
    assert freshness_reason(make_job(date_posted=NOW - timedelta(days=2)), settings, NOW) == ""
    assert "posted 12 days ago" in freshness_reason(make_job(date_posted=NOW - timedelta(days=12)), settings, NOW)
    assert freshness_reason(make_job(date_posted=None, date_trusted=True), settings, NOW) == ""
    assert "no posting date" in freshness_reason(make_job(date_posted=None, date_trusted=False), settings, NOW)


def test_eligibility(settings, profile):
    us_remote = make_job(location="Remote", country="", is_remote=True,
                         description="Remote role. You must be located in the United States.")
    assert "United States" in eligibility_reason(us_remote, profile, settings)
    region_list = make_job(location="EMEA, LATAM, Canada, USA", country="", is_remote=True,
                           description="We hire worldwide-minded people.")
    assert eligibility_reason(region_list, profile, settings).startswith("remote, but only for")
    assert eligibility_reason(make_job(location="Remote - India", is_remote=True), profile, settings) == ""
    assert eligibility_reason(make_job(location="Remote", country="Worldwide", is_remote=True), profile, settings) == ""
    sg = make_job(location="Singapore", country="Singapore", description="AWS role. Visa sponsorship available.")
    assert eligibility_reason(sg, profile, settings) == ""
    sg_no = make_job(location="Singapore", country="Singapore", description="We are unable to sponsor visas.")
    assert eligibility_reason(sg_no, profile, settings) == "no visa sponsorship"
    brazil = make_job(location="São Paulo, Brazil", country="", description="On-site role.")
    assert "not a target location" in eligibility_reason(brazil, profile, settings)
    # remote but tied to one foreign country (what LinkedIn's worldwide remote search returns)
    chile = make_job(location="Santiago, Santiago Metropolitan Region, Chile", country="", is_remote=True)
    assert eligibility_reason(chile, profile, settings) == "remote, but only for Chile"
    unknown_place = make_job(location="Springfield", country="", is_remote=True)
    assert eligibility_reason(unknown_place, profile, settings) == "remote, but based in Springfield"
    hybrid = make_job(location="Hybrid", country="", description="Join our platform team. Kubernetes, Go.")
    assert eligibility_reason(hybrid, profile, settings) == "on-site/hybrid with no stated country"
    hybrid_blr = make_job(location="Hybrid", country="", description="This role is based in our Bengaluru office.")
    assert eligibility_reason(hybrid_blr, profile, settings) == ""


def test_title_words_not_substrings(settings):
    assert title_reason("Senior Software Engineer, Cloudflare for SaaS", settings) == "title not a DevOps/Cloud role"
    assert title_reason("Cloud Engineer", settings) == ""


def test_experience(settings):
    assert experience_reason(make_job(description="Experience: 2-4 years of DevOps."), settings) == ""
    # 3 years + experience_stretch 2: a 5+ posting is still worth applying to, 6+ is not.
    assert experience_reason(make_job(description="Requires 5+ years of experience."), settings) == ""
    assert experience_reason(make_job(description="Requires 6+ years of experience."), settings) != ""
    assert experience_reason(make_job(experience_text="0-1 Yrs", description=""), settings).startswith("too junior")
    assert experience_reason(make_job(description="No years mentioned."), settings) == ""


def test_genuineness(db):
    scam = make_job(description="Pay a registration fee of Rs 2000 to confirm your DevOps seat.")
    assert genuineness(scam, db, NOW).startswith("scam signal")
    fintech = make_job(description="Help millions send money abroad. AWS, Kubernetes, Terraform, Docker roles.")
    assert genuineness(fintech, db, NOW) == ""


def test_dedupe_prefers_employer_form():
    li = make_job(source="linkedin", apply_type=APPLY_LINKEDIN, description="short")
    gh = make_job(source="greenhouse", apply_type=APPLY_GREENHOUSE, url="https://job-boards.greenhouse.io/acme/jobs/1",
                  description="x" * 500)
    kept, dropped = dedupe([li, gh])
    assert [j.source for j in kept] == ["greenhouse"] and dropped[0].reject_reason.startswith("duplicate")


def test_tiers_and_pay_floor(settings):
    assert tier_of(make_job(is_remote=True, location="Remote - India"), settings) == (1, "")
    assert tier_of(make_job(), settings) == (2, "")
    low = make_job(is_remote=True, location="Remote", country="Worldwide", salary_max=900, salary_interval="monthly",
                   salary_currency="USD")
    assert tier_of(low, settings)[1].startswith("remote pay")


def test_screen_end_to_end(settings, profile, master, db):
    good = make_job(url="https://example.com/a")
    senior = make_job(url="https://example.com/b", title="DevOps Engineer II",
                      description="Minimum of 8 years of experience with AWS and Kubernetes.")
    result = screen([good, senior], settings, profile, master, db, log=lambda *a: None, enrich_linkedin=False)
    assert [j.url for j in result.accepted] == ["https://example.com/a"]
    assert result.accepted[0].score > 0.6 and "kubernetes" in result.accepted[0].matched
    assert db.status_counts() == {"queued": 1, "rejected": 1}
