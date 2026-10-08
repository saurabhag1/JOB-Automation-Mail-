"""Load settings.yaml (behaviour), profile.yaml (you) and .env (secrets).

Both YAML files are kept as plain dicts behind a small dotted-path accessor:
the YAML *is* the schema, and every module reads only the keys it needs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent      # job_autopilot/
REPO = ROOT.parent                                  # the JOB-Automation-Mail- repo


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


class _Tree:
    """Dict wrapper with ``get("a.b.c", default)`` access."""

    def __init__(self, data: dict):
        self.data = data or {}

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return default if node is None else node


class Settings(_Tree):
    def __init__(self, data: dict, root: Path = ROOT):
        super().__init__(data)
        self.root = root

    def path(self, dotted: str, default: str) -> Path:
        """A path setting resolved against the job_autopilot folder."""
        return (self.root / str(self.get(dotted, default))).resolve()

    @property
    def db_path(self) -> Path:
        return self.path("paths.db", "data/autopilot.db")

    @property
    def output_dir(self) -> Path:
        return self.path("paths.output", "output")

    @property
    def resume_pdf(self) -> Path:
        return self.path("resume.pdf", "resume.pdf")

    @property
    def master_json(self) -> Path:
        return self.path("resume.master_json", "data/master_resume.json")


# Profile keys that applications cannot do without. Salary and notice period
# are asked for interactively the first time a form needs them.
REQUIRED_PROFILE_KEYS = ("name", "first_name", "last_name", "email", "phone", "city", "country")


class Profile(_Tree):
    def __getattr__(self, key: str) -> Any:
        # profile.city instead of profile.get("city")
        if key.startswith("_") or key == "data":
            raise AttributeError(key)
        return self.data.get(key)

    @property
    def years(self) -> int:
        try:
            return int(float(self.data.get("years_experience") or 0))
        except (TypeError, ValueError):
            return 0

    def missing(self) -> list[str]:
        return [k for k in REQUIRED_PROFILE_KEYS if not str(self.data.get(k) or "").strip()]


def load_env() -> None:
    load_dotenv(ROOT / ".env")
    # The Google-search API keys already live with the lead collector; reuse them.
    load_dotenv(REPO / "job_lead_analyzer" / ".env", override=False)


def load_settings(path: Path | None = None) -> Settings:
    return Settings(_load_yaml(path or ROOT / "settings.yaml"))


def load_profile(path: Path | None = None) -> Profile:
    return Profile(_load_yaml(path or ROOT / "profile.yaml"))


def save_profile(data: dict, path: Path | None = None) -> Path:
    path = path or ROOT / "profile.yaml"
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    return path


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default) or default
