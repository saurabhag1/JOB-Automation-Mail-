"""Claude-powered rewrite of the summary, bullets and cover note.

One request per job, with schema-constrained JSON output. The system prompt
(rules + the master resume) is identical for every job in a run, so it is
prompt-cached and only the job description is billed at the full input rate.
The output still goes through the truthfulness guard before it is used.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from autopilot.config import Profile
from autopilot.models import Job


class LLMUnavailable(Exception):
    """No credentials / bad model: stop using the LLM for the rest of the run."""


class LLMError(Exception):
    """This one request failed: fall back to the rules for this job only."""


SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "experience": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "role_id": {"type": "string"},
                    "bullets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"source_id": {"type": "string"}, "text": {"type": "string"}},
                            "required": ["source_id", "text"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["role_id", "bullets"],
                "additionalProperties": False,
            },
        },
        "cover_note": {"type": "string"},
        "email_subject": {"type": "string"},
        "matched_requirements": {"type": "array", "items": {"type": "string"}},
        "missing_requirements": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "experience", "cover_note", "email_subject", "matched_requirements",
                 "missing_requirements"],
    "additionalProperties": False,
}

SYSTEM = """You tailor one candidate's resume and cover note to one job posting at a time.

The candidate's master resume is below as JSON. It is the only source of facts. You may rephrase, reorder and emphasise those facts so that what this posting asks for comes first. You may never add facts.

Hard rules:
1. Do not add any tool, technology, platform, certification, employer, job title, responsibility, metric or number that is not already in the master resume.
2. Each rewritten bullet comes from exactly one source bullet of the same role (give its id). Keep every number from that bullet unchanged, and mention only technologies that the same role's bullets already mention. Never move an achievement to a different role.
3. Posting requirements the candidate does not meet go in missing_requirements. Do not imply experience with them anywhere else, including the cover note.
4. The candidate has {years} years of experience. Never state a different number.
5. Bullets: one sentence each, at most 40 words, starting with a strong past-tense verb. Keep a bullet's "Label:" prefix only if it still fits.
6. Return every source bullet of every role exactly once, ordered within its role from most to least relevant to the posting.
7. Summary: 2-3 sentences, at most 70 words, aimed at this posting.
8. Cover note: a short, plain-text email body a busy recruiter can read in 20 seconds, 70-150 words, in this shape:
   - "Dear Hiring Team," or "Dear Hiring Team at <Company>," when the company is known;
   - one sentence: the role being applied for, and the candidate's {years} years in DevOps / cloud;
   - a line "What I bring for this role:" followed by exactly 3 lines starting with "- ", each pairing one
     requirement from this posting with one matching achievement from the master resume (keep its numbers exact);
   - one sentence: availability ({notice}) and that the resume is attached;
   - one short closing sentence inviting a quick call.
   Simple words, no buzzwords, no "I am excited/passionate". No subject line, sign-off, signature or
   placeholders - the signature is added automatically.
9. email_subject: under 90 characters, in the form "Application for <role> - {name} | {years} yrs DevOps & Cloud | <availability>".

<master_resume>
{resume}
</master_resume>"""


def credentials_present() -> bool:
    """The SDK also accepts `ant auth login` profiles, so check for those too."""
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return True
    return (Path.home() / ".config" / "anthropic").exists()


def _prompt_resume(master: dict) -> str:
    keep = {k: v for k, v in master.items() if k not in ("_source", "links")}
    # sort_keys keeps the cached prefix byte-identical between runs.
    return json.dumps(keep, sort_keys=True, ensure_ascii=False, indent=1)


class ClaudeTailor:
    def __init__(self, model: str, effort: str, master: dict, profile: Profile):
        import anthropic

        self.anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=3, timeout=240.0)
        self.model = model
        self.effort = effort
        self.system = SYSTEM.format(years=profile.years, notice=profile.notice_period or "not stated",
                                    name=profile.name or "the candidate", resume=_prompt_resume(master))
        self.usage = {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "calls": 0}

    def tailor(self, job: Job, company_known: bool) -> dict:
        description = job.description or "(no description available - use the title only)"
        user = (f"Job title: {job.title}\nCompany: {job.company if company_known else 'not stated'}\n"
                f"Location: {job.location or 'not stated'}\n\nJob description:\n{description}")
        a = self.anthropic
        try:
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            )
        except TypeError as exc:
            if "authentication" in str(exc).lower():
                raise LLMUnavailable("no Anthropic credentials (set ANTHROPIC_API_KEY in .env)") from exc
            raise
        except (a.AuthenticationError, a.PermissionDeniedError) as exc:
            raise LLMUnavailable(f"Anthropic rejected the API key: {exc.message}") from exc
        except a.NotFoundError as exc:
            raise LLMUnavailable(f"model '{self.model}' not available: {exc.message}") from exc
        except a.BadRequestError as exc:
            raise LLMError(f"bad request: {exc.message}") from exc
        except a.RateLimitError as exc:
            raise LLMError("rate limited (after retries)") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"API error {exc.status_code}") from exc
        except (a.APIConnectionError, a.APITimeoutError) as exc:
            raise LLMError(f"network: {exc}") from exc

        u = resp.usage
        self.usage["calls"] += 1
        self.usage["input"] += u.input_tokens or 0
        self.usage["output"] += u.output_tokens or 0
        self.usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        self.usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0

        if resp.stop_reason == "refusal":
            raise LLMError("model declined this posting")
        if resp.stop_reason == "max_tokens":
            raise LLMError("response cut off at max_tokens")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError("response was not valid JSON") from exc
