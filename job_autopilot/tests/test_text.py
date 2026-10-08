from autopilot.text import (detect_countries, find_terms, implied, numbers_in, parse_experience, pick_emails,
                            unsupported_terms)


def test_terms_respect_word_boundaries():
    terms = find_terms("Experience with GitHub Actions and JavaScript; e-mail us. Detail-oriented.")
    assert "github_actions" in terms and "javascript" in terms
    assert "git" not in terms        # 'git' inside 'GitHub' does not count
    assert "java" not in terms       # 'java' inside 'JavaScript' does not count
    assert "ai" not in terms         # 'ai' inside 'e-mail' / 'Detail' does not count


def test_terms_aliases_and_hyphens():
    terms = find_terms("k8s, auto-scaling, Argo CD, blue-green rollouts, Amazon Web Services")
    assert {"kubernetes", "autoscaling", "argocd", "blue_green", "aws"} <= set(terms)


def test_implied_parents_only_one_way():
    assert {"aws", "kubernetes"} <= implied({"eks"})
    assert "eks" not in implied({"aws"})


def test_unsupported_terms_synonym_vs_narrower():
    src = "Managed Linux servers and the ELK Stack on AWS EKS"
    assert unsupported_terms("Administered Linux hosts", src) == []
    assert unsupported_terms("Ran RHEL servers", src) == ["rhel"]            # narrower term needs its own evidence
    assert unsupported_terms("Built Pulumi stacks", src) == ["Pulumi"]
    assert unsupported_terms("Kubernetes on AWS", src) == []                  # implied by EKS


def test_numbers():
    assert numbers_in("cut MTTR by 35% and kept 99.9% uptime, 20+ hours on EC2 and S3") == {"35%", "99.9%", "20+"}
    assert numbers_in("40 percent faster") == {"40%"}


def test_experience_parsing():
    assert parse_experience("Experience: 3-5 years in DevOps") == (3, 5)
    assert parse_experience("Minimum of 5 years of experience with AWS") == (5, None)
    assert parse_experience("We need 4+ years experience") == (4, None)
    assert parse_experience("6-11 Yrs", explicit=True) == (6, 11)
    # a number of years that is not about experience is ignored
    assert parse_experience("Founded 25 years ago, we build payments. Python, Go.") == (None, None)


def test_pick_emails_uses_context():
    jd = ("Interested candidates can share their CV at megha@quicksort.co.in. "
          "For accommodation requests during the hiring process contact talent@bigco.com. "
          "Beware of fraud: we never ask for money. Report it to security@bigco.com.")
    primary, alternates = pick_emails(jd)
    assert primary == "megha@quicksort.co.in"
    assert "talent@bigco.com" not in alternates


def test_generic_inbox_needs_application_wording():
    assert pick_emails("Questions? info@acme.io")[0] == ""
    assert pick_emails("Send your resume to careers@acme.io")[0] == "careers@acme.io"
    assert pick_emails("Reach us at noreply@acme.io with your resume")[0] == ""


def test_countries():
    assert detect_countries("EMEA, LATAM, Canada, USA") == ("Canada", "United States", "Europe", "LATAM")
    assert detect_countries("Bengaluru, Karnataka, India") == ("India",)
    assert detect_countries("Indiana") == ()
