"""Free-tier LLMs for tailoring: Google Gemini, Groq and OpenRouter.

Each job asks up to `tailoring.best_of` different providers for a draft (same
prompt, same JSON schema). Every draft goes through the truthfulness guard and
the cleanest one wins, so no single free model's bad day reaches a recruiter.
A provider that is rate-limited or overloaded cools down and the next one is
used; a rejected key switches that provider off for the run.
"""

from __future__ import annotations

import json
import os
import re
import time

import requests

from autopilot.config import Profile, Settings
from autopilot.models import Job
from autopilot.tailoring.llm import SCHEMA, SYSTEM, LLMError, LLMUnavailable, _prompt_resume

DEFAULT_PROVIDERS = [
    {"name": "gemini", "models": ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-flash-latest"]},
    {"name": "groq", "models": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]},
    {"name": "openrouter", "models": ["nvidia/nemotron-3-ultra-550b-a55b:free", "google/gemma-4-31b-it:free",
                                      "nvidia/nemotron-3-super-120b-a12b:free"]},
]
KEY_ENV = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
OPENAI_URL = {"groq": "https://api.groq.com/openai/v1/chat/completions",
              "openrouter": "https://openrouter.ai/api/v1/chat/completions"}

STYLE = """

The candidate's own application email is below. Keep its voice, structure and availability statement
when writing cover_note, adapt it to this posting, fix any spelling, and keep only facts that are also in
the master resume:
<usual_email>
{template}
</usual_email>"""


class _Retryable(Exception):
    pass


class _Fatal(Exception):
    pass


def free_keys_present() -> bool:
    return any(os.getenv(v) for v in KEY_ENV.values())


class FreeLLMChain:
    def __init__(self, settings: Settings, master: dict, profile: Profile, template_text: str = "", log=print):
        self.log = log
        self.best_of = max(1, int(settings.get("tailoring.best_of", 2)))
        configured = settings.get("tailoring.providers", DEFAULT_PROVIDERS) or DEFAULT_PROVIDERS
        self.providers = [p for p in configured if os.getenv(KEY_ENV.get(p["name"], ""))]
        if not self.providers:
            raise LLMUnavailable("no GEMINI_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY in .env")
        self.system = SYSTEM.format(years=profile.years, notice=profile.notice_period or "not stated",
                                    name=profile.name or "the candidate", resume=_prompt_resume(master))
        if template_text.strip():
            self.system += STYLE.format(template=template_text.strip())
        self.cool_until: dict[str, float] = {}
        self.dead: set[str] = set()
        self.usage = {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "by_model": {}}

    @property
    def names(self) -> list[str]:
        return [p["name"] for p in self.providers]

    # -- one HTTP call -----------------------------------------------------
    def _gemini(self, model: str, user: str) -> tuple[str, dict]:
        body = {"systemInstruction": {"parts": [{"text": self.system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": SCHEMA,
                                     "temperature": 0.4}}
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                          headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]}, json=body, timeout=180)
        self._raise_for(r)
        data = r.json()
        cand = (data.get("candidates") or [{}])[0]
        if cand.get("finishReason") not in (None, "STOP"):
            raise _Retryable(f"finish reason {cand.get('finishReason')}")
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought"))
        meta = data.get("usageMetadata", {})
        return text, {"input": meta.get("promptTokenCount", 0), "output": meta.get("candidatesTokenCount", 0)}

    def _openai_style(self, provider: str, model: str, user: str) -> tuple[str, dict]:
        headers = {"Authorization": f"Bearer {os.environ[KEY_ENV[provider]]}"}
        if provider == "openrouter":
            headers.update({"HTTP-Referer": "https://github.com/saurabhag1", "X-Title": "Job Autopilot"})
        messages = [{"role": "system", "content": self.system}, {"role": "user", "content": user}]
        strict = {"type": "json_schema", "json_schema": {"name": "tailored", "schema": SCHEMA, "strict": True}}
        r = requests.post(OPENAI_URL[provider], headers=headers, timeout=180,
                          json={"model": model, "messages": messages, "temperature": 0.4, "response_format": strict})
        if r.status_code == 400:   # model without schema support: plain JSON mode, schema in the prompt
            messages[0]["content"] += "\n\nReply with one JSON object matching this schema:\n" + json.dumps(SCHEMA)
            r = requests.post(OPENAI_URL[provider], headers=headers, timeout=180,
                              json={"model": model, "messages": messages, "temperature": 0.4,
                                    "response_format": {"type": "json_object"}})
        self._raise_for(r)
        data = r.json()
        if data.get("error"):
            raise _Retryable(str(data["error"])[:150])
        choice = (data.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            raise _Retryable("cut off at max tokens")
        usage = data.get("usage") or {}
        return (choice.get("message") or {}).get("content") or "", {"input": usage.get("prompt_tokens", 0),
                                                                   "output": usage.get("completion_tokens", 0)}

    @staticmethod
    def _raise_for(r: requests.Response) -> None:
        if r.status_code in (401, 403):
            raise _Fatal(f"key rejected ({r.status_code})")
        if r.status_code in (404,):
            raise _Retryable(f"model not available ({r.text[:100]})")
        if r.status_code in (408, 409, 429, 500, 502, 503, 504):
            raise _Retryable(f"HTTP {r.status_code} (busy / rate-limited)")
        if not r.ok:
            raise _Retryable(f"HTTP {r.status_code}: {r.text[:120]}")

    @staticmethod
    def _parse(text: str) -> dict:
        text = text.strip()
        fenced = re.search(r"\{.*\}", text, re.S)          # tolerate ```json fences / chatter
        data = json.loads(fenced.group(0) if fenced else text)
        missing = [k for k in SCHEMA["required"] if k not in data]
        if missing or not isinstance(data.get("experience"), list):
            raise ValueError(f"missing fields {missing}")
        return data

    # -- drafts ------------------------------------------------------------
    def _from_provider(self, provider: dict, user: str) -> tuple[str, dict]:
        name = provider["name"]
        last = ""
        for model in provider["models"]:
            try:
                started = time.time()
                if name == "gemini":
                    text, used = self._gemini(model, user)
                else:
                    text, used = self._openai_style(name, model, user)
                data = self._parse(text)
            except _Fatal as exc:
                self.dead.add(name)
                self.log(f"    {name}: {exc} - provider off for this run")
                raise
            except (_Retryable, ValueError, requests.RequestException) as exc:
                last = f"{model}: {exc}"
                continue
            label = f"{name}:{model}"
            self.usage["calls"] += 1
            self.usage["input"] += used["input"]
            self.usage["output"] += used["output"]
            self.usage["by_model"][label] = self.usage["by_model"].get(label, 0) + 1
            self.log(f"    draft from {label} ({time.time() - started:.0f}s)")
            return label, data
        self.cool_until[name] = time.time() + 90      # every model busy: give this provider a rest
        raise _Retryable(last or "no model answered")

    def drafts(self, job: Job, company_known: bool, want: int | None = None) -> list[tuple[str, dict]]:
        want = want or self.best_of
        description = job.description or "(no description available - use the title only)"
        user = (f"Job title: {job.title}\nCompany: {job.company if company_known else 'not stated'}\n"
                f"Location: {job.location or 'not stated'}\n\nJob description:\n{description[:30000]}")
        out, errors = [], []
        for provider in self.providers:
            if len(out) >= want:
                break
            name = provider["name"]
            if name in self.dead or self.cool_until.get(name, 0) > time.time():
                continue
            try:
                out.append(self._from_provider(provider, user))
            except _Fatal:
                continue
            except _Retryable as exc:
                errors.append(f"{name}: {exc}")
                self.log(f"    {name} unavailable for this job ({str(exc)[:90]})")
        if not out:
            if len(self.dead) == len(self.providers):
                raise LLMUnavailable("every free LLM key was rejected")
            raise LLMError("; ".join(errors) or "all free providers are cooling down")
        return out

    def tailor(self, job: Job, company_known: bool) -> dict:
        return self.drafts(job, company_known, want=1)[0][1]
