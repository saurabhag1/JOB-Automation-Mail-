from autopilot.resume.parser import resume_text
from autopilot.tailoring import guard


def _roles(master, rewrites):
    """LLM-shaped payload: every bullet of every role, with some rewritten."""
    out = []
    for role in master["experience"]:
        bullets = []
        for b in role["bullets"]:
            text = rewrites.get(b["id"], f"{b['label']}: {b['text']}" if b["label"] else b["text"])
            bullets.append({"source_id": b["id"], "text": text})
        out.append({"role_id": role["id"], "bullets": bullets})
    return out


def _final(master, rewrites):
    final, notes = guard.check_bullets(_roles(master, rewrites), master, resume_text(master), 3)
    by_id = {b["id"]: b for bullets in final.values() for b in bullets}
    return by_id, notes


def test_faithful_rewrite_is_kept(master):
    text = ("Strengthened Jenkins and Docker CI/CD pipelines, enabling 40% faster deployments "
            "with zero critical incidents.")
    by_id, notes = _final(master, {"e2.b3": text})
    assert by_id["e2.b3"]["text"] == text and not notes


def test_new_number_is_reverted(master):
    by_id, notes = _final(master, {"e2.b3": "Strengthened Jenkins pipelines for 60% faster deployments."})
    assert "40%" in by_id["e2.b3"]["text"]
    assert any("new numbers 60%" in n for n in notes)


def test_tool_from_nowhere_is_reverted(master):
    by_id, notes = _final(master, {"e2.b4": "Standardized Kubernetes deployments using Helm and Pulumi, "
                                            "minimizing manual intervention by 50%."})
    assert "Pulumi" not in by_id["e2.b4"]["text"]
    assert any("Pulumi" in n for n in notes)


def test_tool_from_another_role_is_reverted(master):
    # Terraform appears in the current role, not in the Immersive (e2) bullets.
    by_id, notes = _final(master, {"e2.b4": "Standardized Kubernetes deployments with Helm and Terraform, "
                                            "minimizing manual intervention by 50%."})
    assert "Terraform" not in by_id["e2.b4"]["text"]


def test_implied_parent_is_allowed(master):
    text = "Fine-tuned containerized AWS EKS workloads, lowering cloud costs by 15% and speeding releases by 20%."
    by_id, _ = _final(master, {"e2.b1": text})
    assert by_id["e2.b1"]["text"] == text           # EKS work is AWS work


def test_unknown_acronym_is_reverted(master):
    by_id, notes = _final(master, {"e2.b5": "Defined SLOs and centralized Prometheus & Grafana monitoring, "
                                            "mitigating MTTR by 35%."})
    assert "SLO" not in by_id["e2.b5"]["text"]


def test_dropped_and_duplicate_bullets(master):
    payload = _roles(master, {})
    payload[1]["bullets"] = payload[1]["bullets"][:2] + payload[1]["bullets"][:1]   # drop 4, duplicate 1
    final, _ = guard.check_bullets(payload, master, resume_text(master), 3)
    ids = [b["id"] for b in final["e2"]]
    assert sorted(ids) == sorted(b["id"] for b in master["experience"][1]["bullets"])


def test_years_and_placeholders():
    src = "DevOps engineer, AWS, Kubernetes"
    assert guard.problems("DevOps engineer with 5+ years on AWS", src, src, src, 3) == ["new numbers 5+", "claims 5 years"]
    assert guard.problems("Dear [Hiring Manager], AWS", src, src, src, 3) == ["placeholder text"]
    assert guard.problems("DevOps engineer with 3+ years on AWS and Kubernetes", src, src, src, 3) == []


def test_metrics_must_come_from_one_achievement(master):
    units = guard.resume_units(master)
    welded = "I built pipelines with GitHub Actions and Jenkins, reducing deployment cycles by 50% while sustaining 99.9% uptime."
    assert guard.misattributed(welded, units, 3) == ["metrics 50%, 99.9% not from one achievement"]
    honest = "I built pipelines with GitHub Actions and Jenkins, reducing deployment cycles by 50%."
    assert guard.misattributed(honest, units, 3) == []
    assert guard.misattributed("I cut cloud costs by 40% with Karpenter.", units, 3) != []
