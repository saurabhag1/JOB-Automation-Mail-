"""The Claude path with a stand-in client: request shape, parsing, and the guard on its output."""

import json
import re
from types import SimpleNamespace

import pytest

from autopilot import browser
from autopilot.tailoring import Tailor
from autopilot.tailoring.llm import ClaudeTailor, LLMUnavailable
from conftest import make_job


class FakeMessages:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="thinking", thinking=""),
                     SimpleNamespace(type="text", text=json.dumps(self.payload))],
            usage=SimpleNamespace(input_tokens=900, output_tokens=1200, cache_read_input_tokens=4000,
                                  cache_creation_input_tokens=0))


def _payload(master):
    roles = []
    for role in master["experience"]:
        bullets = [{"source_id": b["id"], "text": f"{b['label']}: {b['text']}" if b["label"] else b["text"]}
                   for b in role["bullets"]]
        roles.append({"role_id": role["id"], "bullets": bullets})
    # one honest rewrite, one that invents a metric
    roles[1]["bullets"][2]["text"] = ("Strengthened Jenkins and Docker CI/CD pipelines, enabling 40% faster "
                                      "deployments with zero critical incidents.")
    roles[1]["bullets"][3]["text"] = "Standardized Kubernetes deployments using Helm, cutting toil by 70%."
    return {
        "summary": ("DevOps & Cloud/SRE Engineer with 3+ years of experience running AWS EKS, Terraform and "
                    "GitHub Actions/Jenkins CI/CD, with Prometheus and Grafana observability."),
        "experience": roles,
        "cover_note": ("Dear Hiring Team at Acme,\n\nI'm applying for the DevOps Engineer role. Over 3+ years I have "
                       "run AWS EKS clusters with Terraform and built GitHub Actions and Jenkins pipelines that cut "
                       "deployment cycles by 50%.\n\nAt Immersive I strengthened CI/CD pipelines for 40% faster "
                       "deployments and standardized Kubernetes releases with Helm. I also centralized Prometheus "
                       "and Grafana monitoring, mitigating MTTR by 35%.\n\nI can join immediately and would welcome "
                       "a conversation.\n\nBest regards,\nSaurabh"),
        "email_subject": "DevOps Engineer application - Saurabh Agrawal (3+ yrs AWS/Kubernetes)",
        "matched_requirements": ["AWS", "Kubernetes"],
        "missing_requirements": ["Datadog"],
    }


def test_request_shape_and_guarded_merge(master, profile, settings, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    tailor = Tailor(master, profile, settings, tmp_path, log=lambda *a: None)
    assert isinstance(tailor.llm, ClaudeTailor)
    fake = FakeMessages(_payload(master))
    tailor.llm.client = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    try:
        pkg = tailor.build(make_job())
    finally:
        browser.shutdown()

    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"]["effort"] == "medium"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Ienegizer IT Service Pvt Ltd" in call["system"][0]["text"]      # the master resume is the context

    assert pkg.method == "claude"
    assert pkg.cover_note.endswith("would welcome a conversation.")         # sign-off stripped (added by us)
    assert pkg.subject.startswith("DevOps Engineer application")
    assert any("kept original e2.b4" in n and "70%" in n for n in pkg.notes)  # invented metric reverted
    html = re.sub(r"<[^>]+>", "", (pkg.folder / "Saurabh_Agrawal_Resume.html").read_text())   # drop highlight tags
    assert "Strengthened Jenkins and Docker CI/CD pipelines" in html
    assert "70%" not in html and "minimizing manual intervention by 50%" in html
    assert tailor.llm.usage["cache_read"] == 4000


def test_missing_credentials_disable_claude(master, profile, settings):
    tailor_llm = ClaudeTailor("claude-opus-5-5", "medium", master, profile)

    def no_auth(**kwargs):
        raise TypeError("Could not resolve authentication method. Expected one of api_key...")

    tailor_llm.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=no_auth)))
    with pytest.raises(LLMUnavailable):
        tailor_llm.tailor(make_job(), company_known=True)
