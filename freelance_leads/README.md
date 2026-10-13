# Freelance / Contract DevOps Leads

This agent finds **contract, freelance, part-time and project** DevOps / Cloud work that is remote or in India. It emails each new HR contact a short note with your resume, and sends you an Excel report.

- **Runs:** every day at **2:00 PM IST** (`.github/workflows/freelance-leads.yml`). You can also start it from **Actions → Freelance / Contract DevOps Leads → Run workflow**; tick *dry_run* to draft without sending.
- **Locally:** `python3 freelance_leads/freelance_leads.py` drafts only. Add `--send` to email HR.

## What it does

1. **Searches** these sources:
   - Google, past week (your SERP keys): LinkedIn recruiter posts that say "contract / freelance / C2H … share your resume", plus Cutshort, Wellfound, Arc, Contra, Remote.co, Himalayas, Naukri, foundit and Instahyre.
   - OpenRouter web search.
   - Indeed and LinkedIn with their *contract* and *part-time* filters, and Naukri "contract" / "freelance".
   - The Remotive, Jobicy and Himalayas feeds (contract, freelance and part-time posts only).
   - DuckDuckGo, only if no SERP key is set.
2. **Keeps only genuine leads.** A lead must be:
   - posted in the last 10 days;
   - really contract-type (an "employment contract" or "smart contract" mention doesn't count);
   - remote and open to India, or based in India;
   - asking for at most 5 years of experience;
   - free of scam wording;
   - a match for your resume skills (at least 30%).

   An AI also drops training ads and job-seeker posts. Each lead appears only once across runs.
3. **Emails HR**, at most 30 per run. It uses the same clean layout as the job autopilot: your 3 most relevant achievements, matching skills, availability for contract work, and your contact details. Your resume PDF is attached.
4. **Never emails the same recruiter twice**, whether they were contacted by this agent, the Daily Job Autopilot or the older mailers.
5. **Excel report** `runs/<stamp>/devops_leads_YYYYMMDD_HHMMSS.xlsx`:
   - **Columns:** SR · Company Name · Position (links to the post) · Works / Description · Location · Salary / Rate · Contract Type · Contact / HR Email · Job Role · Status.
   - **Status** is *Sent*, *Email Drafted* or *Pending Review* (no HR email: apply via the link).
   - A **Details** sheet has the links, dates, skill match and notes.

   The workbook is emailed to you after every run.

Settings live in `config.yaml`: keywords, places, freshness, search budgets and the send cap. Keys come only from the variables that already exist: `EMAIL_ADDRESS`, `EMAIL_PASSWORD`, the SERP keys, `GEMINI/GROQ/OPENROUTER_API_KEY` and `REPORT_EMAIL`. Your details come from `job_autopilot/profile.yaml` (the `AUTOPILOT_PROFILE_YAML` secret in Actions).

The code is shared with `job_autopilot/`: search, screening and the email template. Install it once with `job_autopilot/requirements.txt`.
