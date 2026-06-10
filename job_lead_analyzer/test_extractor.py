"""Spot-checks for extractor.py against real sample posts (no network)."""

import extractor

NIPPON = (
    "Hiring for NIC- DevOps Engineer\nLocation -New Delhi\nExp-3-5 years\n"
    "CTC-39K\nhr4@nippondata.com\n8800097942\n"
    "Skills Required: Jenkins, Docker, Kubernetes, AWS/Azure/GCP, Linux, Prometheus"
)

VECTED = (
    '"DevOps Engineer"\n1. Company: Vected Technologies\n'
    "2. Location: Indore, Madhya Pradesh.\n3. Experience: 1 to 3 years\n"
    "4. Drop your resume at hr@vectedtech.com"
)

NOREPLY = "Apply now! Auto-confirmation from no-reply@bigcorp.com. Experience 2-4 years devops aws."


def check(name, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {name}")
    assert cond, name


# --- nippon: personal-ish email, phone, experience overlaps 0-5, skills match ---
picked = extractor.pick_emails(NIPPON)
check("nippon primary email", picked["primary"] == "hr4@nippondata.com")
check("nippon email classified generic (hr4)", picked["email_type"] == "generic")
check("nippon phone", extractor.extract_phone(NIPPON) == "8800097942")
emn = extractor.parse_experience(NIPPON)
check("nippon experience (3,5)", emn == (3, 5))
check("nippon overlaps 0-5", extractor.experience_overlaps(*emn, 0, 5))
matched = extractor.match_skills(NIPPON)
check("nippon skills matched", "devops" in matched and "aws" in matched)
check("nippon is relevant", extractor.is_relevant(matched))

# --- vected: company line, personal email, exp 1-3 ---
pv = extractor.pick_emails(VECTED)
check("vected email", pv["primary"] == "hr@vectedtech.com")
check("vected company", extractor.guess_company(VECTED, pv["primary"]) == "Vected Technologies")
check("vected experience (1,3)", extractor.parse_experience(VECTED) == (1, 3))

# --- no-reply must be rejected ---
pn = extractor.pick_emails(NOREPLY)
check("no-reply rejected", pn["primary"] is None)
check("classify no-reply", extractor.classify_email("no-reply@bigcorp.com") == "reject")
check("classify personal", extractor.classify_email("venkat@adarshsolutions.com") == "personal")

# --- truncated snippet emails must be rejected (the reported bug) ---
TRUNCATED = "DevOps role, 2-4 years aws devops. Contact abhishe...@1pointsys.com or teena....@globant.com"
pt = extractor.pick_emails(TRUNCATED)
check("truncated emails dropped", pt["primary"] is None)
check("is_complete rejects ellipsis-dots", not extractor.is_complete_email("abhishe...@1pointsys.com"))
check("is_complete accepts full email", extractor.is_complete_email("krishna.kumari@tietoevry.com"))
check("format experience 1-3", extractor.format_experience(1, 3) == "1-3 yrs")
check("format experience 3+", extractor.format_experience(3, None) == "3+ yrs")

# --- word-boundary matching: short tokens must not match inside words ---
check("'ai' not matched in 'email available domain'",
      "ai" not in extractor.match_skills("Send your email, role available, domain work"))
check("'git' not matched in 'digital legitimate'",
      "git" not in extractor.match_skills("digital legitimate strategy"))
check("'aws' still matched as a word", "aws" in extractor.match_skills("strong AWS and Docker skills"))

# --- relevance gate: a non-devops job with one loose skill is dropped ---
check("python-only marketing job not relevant",
      not extractor.is_relevant(extractor.match_skills("Marketing analyst with some python reporting")))
check("devops+docker job relevant",
      extractor.is_relevant(extractor.match_skills("DevOps engineer, Docker and Kubernetes")))

# --- title cleaning ---
check("clean_title strips ' at Company'",
      extractor.clean_title("DevOps Engineer at Iris Software Inc") == "DevOps Engineer")
check("clean_title strips '- LinkedIn'",
      extractor.clean_title("AWS Cloud Engineer - LinkedIn") == "AWS Cloud Engineer")

print("\nAll extractor checks passed.")
