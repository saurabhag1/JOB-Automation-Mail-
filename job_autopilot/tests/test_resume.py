from autopilot.resume.parser import _split_items, resume_text, tidy


def test_parses_contact_and_sections(master):
    c = master["contact"]
    assert master["name"] == "Saurabh Agrawal"
    assert c["email"] == "saurabhag012@gmail.com" and c["phone"] == "+91 7030126522"
    assert c["linkedin"].startswith("https://www.linkedin.com/in/")
    assert "Professional Summary" not in master["summary"] and master["summary"].startswith("DevOps")


def test_parses_roles_and_bullets(master):
    roles = master["experience"]
    assert [len(r["bullets"]) for r in roles] == [7, 6, 6]
    assert roles[0]["dates"] == "Dec 2025 - Present"
    assert roles[1]["company"] == "Immersive Pvt Ltd"
    assert roles[2]["title"] == "Full Stack Web Developer (DevOps-Focused)"   # title on the next page
    first = roles[0]["bullets"][0]
    assert first["label"] == "Cloud & Kubernetes Infrastructure" and first["text"].startswith("Managed")


def test_skills_projects_and_certs(master):
    cloud = master["skills"][0]
    assert cloud["items"][-1] == "GCP"            # unclosed '(' in the PDF no longer swallows GCP
    assert len(master["projects"]) == 5
    assert master["certifications"][0]["dates"] == "March 2026 - March 2029"


def test_every_bullet_is_in_resume_text(master):
    text = resume_text(master)
    for role in master["experience"]:
        for b in role["bullets"]:
            assert b["text"] in text


def test_tidy_and_split():
    assert tidy("Pune , Maharashtra ( IN )") == "Pune, Maharashtra (IN)"
    assert tidy("Configured (.wasm, .js, .data)") == "Configured (.wasm, .js, .data)"
    assert _split_items("Docker, Kubernetes (EKS, AKS, GKE), Helm") == ["Docker", "Kubernetes (EKS, AKS, GKE)", "Helm"]
