"""Claude through the Claude Code CLI on this Mac - used only by local runs started from the
portal (portal/portal.py sets AUTOPILOT_LLM=claude-code). GitHub Actions never sets it, so the
scheduled runs keep using the free Gemini / Groq / OpenRouter chain exactly as before.

    AUTOPILOT_LLM=claude-code        switch it on
    AUTOPILOT_CLAUDE_MODEL=opus      model alias or full name the CLI accepts (default: opus)
    AUTOPILOT_CLAUDE_EFFORT=high     low / medium / high / xhigh / max (default: high)

Each call is one `claude -p` run with no tools and no saved session, the prompt on stdin and,
when a JSON schema is given, schema-checked structured output.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile


class ClaudeCodeError(Exception):
    pass


def enabled() -> bool:
    return os.getenv("AUTOPILOT_LLM", "").lower() == "claude-code" and bool(shutil.which("claude"))


def model() -> str:
    return os.getenv("AUTOPILOT_CLAUDE_MODEL") or "opus"


def effort() -> str:
    return os.getenv("AUTOPILOT_CLAUDE_EFFORT") or "high"


def label() -> str:
    return f"claude-code:{model()} ({effort()} effort)"


def ask(prompt: str, system: str = "", schema: dict | None = None, timeout: int = 600) -> dict:
    """The JSON object Claude returns for *prompt*."""
    cmd = ["claude", "-p", "--model", model(), "--effort", effort(), "--output-format", "json",
           "--tools", "", "--no-session-persistence"]
    if system:
        cmd += ["--system-prompt", system]
    if schema:
        cmd += ["--json-schema", json.dumps(schema)]
    # A neutral working directory: no project CLAUDE.md or settings leak into the prompt.
    with tempfile.TemporaryDirectory() as cwd:
        try:
            done = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCodeError(f"no answer within {timeout}s") from exc
    try:
        out = json.loads(done.stdout.strip().splitlines()[-1] if done.stdout.strip() else "{}")
    except ValueError as exc:
        raise ClaudeCodeError(f"unreadable CLI output: {done.stdout[:160] or done.stderr[:160]}") from exc
    if out.get("is_error") or done.returncode != 0:
        raise ClaudeCodeError(str(out.get("result") or done.stderr or done.stdout)[:200])
    if isinstance(out.get("structured_output"), dict):
        return out["structured_output"]
    text = str(out.get("result") or "")
    match = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(match.group(0) if match else text)
    except ValueError as exc:
        raise ClaudeCodeError(f"answer is not JSON: {text[:160]}") from exc
    if not isinstance(data, dict):
        raise ClaudeCodeError("answer is not a JSON object")
    return data
