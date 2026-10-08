#!/usr/bin/env python3
"""Daily job hunt - one command does everything.

    python3 job_hunt.py              find jobs, tailor, apply / email HR for real
    python3 job_hunt.py --dry-run    do everything except sending (check the output first)
    python3 job_hunt.py --review     the browser fills forms, you press Submit yourself
    python3 job_hunt.py --unattended never stop to ask (for cron); stuck jobs are listed instead
    python3 job_hunt.py <command>    any autopilot command: doctor, status, answers, mail-report ...

Each run:
  1. searches LinkedIn, Indeed, Naukri, Glassdoor, Bayt, 44 company job boards (Greenhouse /
     Lever / Ashby), remote-job feeds and Google (past week) across ~30 portals including
     Instahyre, foundit, Hirist, iimjobs, Cutshort, Wellfound and LinkedIn recruiter posts;
  2. keeps only genuine jobs from this week that fit you, ranked by hiring chance;
  3. tailors your resume + your usual email to each job (free Gemini / Groq / OpenRouter
     models, every sentence checked against your real resume);
  4. emails HR from your Gmail, or fills the employer's application form (also found for
     LinkedIn / company-site jobs when the employer uses Greenhouse, Lever or Ashby);
  5. writes everything to job_runs/<date>_<time>_IST/ (Excel + summary + every file sent), with
     the jobs you must apply to yourself in job_runs/<run>/APPLY_MANUALLY/.

It runs by itself every day on GitHub Actions (.github/workflows/daily-job-autopilot.yml).

Keywords come from job_autopilot/settings.yaml -> search.keywords.
Your details and standard answers come from job_autopilot/profile.yaml.
Keys come from job_autopilot/.env (and the Google-search keys from job_lead_analyzer/.env).
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE / "job_autopilot"
VENV = APP / ".venv"
VENV_PY = VENV / "bin" / "python"
COMMANDS = ("init", "doctor", "discover", "apply", "run", "tailor", "answers", "status", "report", "login", "mail-report")


def main() -> int:
    if os.path.realpath(sys.prefix) != os.path.realpath(VENV):
        if not VENV_PY.exists():
            print("First-time setup needed (once):\n"
                  f"  cd {APP}\n  uv venv --python 3.12 .venv\n"
                  "  uv pip install --python .venv/bin/python -r requirements.txt\n"
                  "  .venv/bin/python -m playwright install chromium")
            return 1
        os.execv(str(VENV_PY), [str(VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])

    if any(a in ("-h", "--help") for a in sys.argv[1:2]):
        print(__doc__)
    sys.path.insert(0, str(APP))
    os.chdir(APP)
    from autopilot.cli import main as cli

    if sys.argv[1:] and sys.argv[1] in COMMANDS:
        return cli(sys.argv[1:])
    if not (APP / "profile.yaml").exists() or not (APP / "data" / "master_resume.json").exists():
        cli(["init"])
    return cli(["run", *sys.argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
