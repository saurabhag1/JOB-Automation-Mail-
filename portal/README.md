# Job Automation Control Center (local)

A web page on your Mac to see every workflow and run the job searches with **Claude**.

    python3 portal/portal.py        # or double-click "Job Portal.command" in the repo folder

It opens <http://127.0.0.1:8765>. The page is only reachable from this Mac, and every action needs a secret token that is embedded in the page.

## What you see

- **All 5 GitHub workflows:**
  - their schedule in IST;
  - what each one does;
  - their latest GitHub runs, as coloured dots that link to each run;
  - buttons to **Open on GitHub** or **Run on GitHub**. Running on GitHub needs `GITHUB_TOKEN` with `actions: write` in `job_autopilot/.env`.
- **Run locally**, for the two workflows that are safe to run twice: **Daily Job Autopilot** and **Freelance / Contract DevOps Leads**. You can do a *live* run, which sends email, or a *dry run*. A **live log** streams while it runs, and you can stop it.
- **Results:**
  - the latest freelance leads (platform, budget, verification, link) and Job Autopilot results (KPIs, emails sent, apply-by-hand list), with Excel downloads;
  - a history of local runs.

The bulk mailer, its follow-ups and the lead scraper stay **GitHub only**. They commit their own state back to the repo, so a second local copy would email people twice.

## How local runs differ from the GitHub runs

| | GitHub (scheduled) | Local run from this portal |
|---|---|---|
| AI for tailoring and search helpers | free Gemini / Groq / OpenRouter | **Claude via the Claude Code CLI** (model and effort set on the page; default `opus`, `high`), with the free models as fallback |
| Freelance leads | rule-based screening | rules **plus a Claude check** that each lead is real DevOps / cloud work for 2-4 years |
| "Already emailed" list | the Actions cache | **your Gmail Sent folder** (recipient headers, last 60 days), so nobody the GitHub runs emailed is emailed again |
| Report | emailed to you | emailed to you (same) |

Nothing in `.github/workflows/` changed. The GitHub runs never set the switches the portal uses (`AUTOPILOT_LLM=claude-code`, `AUTOPILOT_GMAIL_DEDUPE=1`), so they behave exactly as before.

**Requirements:**
- `claude` (Claude Code) installed and signed in on this Mac.
- `job_autopilot/.venv` set up once.
- The same `.env` files as for local runs.

Claude calls count against your Claude plan's usage. The page's **Test Claude** button checks the model you chose.

`portal/data/` (settings and run logs) stays on this Mac and is git-ignored.
