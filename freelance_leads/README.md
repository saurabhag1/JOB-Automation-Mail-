# Freelance DevOps / AWS Work Finder

This agent finds **freelance projects and part-time gigs** in DevOps, AWS and cloud work that you can do on the side, **2-3 hours a day or at weekends**. Clients can be anywhere: the US, UK, Europe, Australia, India and elsewhere.

- **Runs:** every day at **2:00 PM IST** (`.github/workflows/freelance-leads.yml`). You can also start it from **Actions → Freelance / Contract DevOps Leads → Run workflow**; tick *dry_run* to only draft.
- **Locally:** `python3 freelance_leads/freelance_leads.py` drafts only. Add `--send` to email the contacts found.

## Where it looks

| Source | How |
|---|---|
| **Freelancer.com** | Public projects API: open projects tagged DevOps, AWS, Kubernetes, Docker, Terraform, Jenkins, Ansible, SRE, Lambda, Azure or GCP |
| **PeoplePerHour** | Search pages, then each project's own page (posted date, open-until date, rate) |
| **Upwork** | Google, past week, `upwork.com/freelance-jobs/apply/…`; one skill per search, rotated daily |
| **Guru, Contra, Truelancer, freelancermap, Reddit r/forhire** | Google, past week; only links to one specific project |
| **LinkedIn posts** | "looking for a freelance / part-time DevOps…" posts in the US, UK, Europe and Australia, plus remote |
| **LinkedIn + Indeed** | Part-time and contract roles, remote, in the US, UK, EU, Australia, Canada and India |
| **Remotive, Jobicy, Himalayas** | Contract, freelance and part-time posts only |

## What it keeps (verified)

A lead must meet all of these:

- It is **one real project or post**, not a list or search page.
- It was posted in the **last 10 days** and is still open.
- It is **remote** and doesn't need full-time hours: "40 hours a week", "full-time" and on-site work are skipped.
- It suits **2-4 years of experience**: senior, lead and architect titles, and "5+ years" asks, are dropped.
- It has **no scam signs**: account renting, KYC, fees, "free test task", exam proxies and so on.
- It pays at least **$100 fixed or $10/hr** when a budget is shown.
- It is really **DevOps / cloud work** that matches your resume.

Each lead's **Verified** column records where it was checked, for example *"Freelancer.com API: open project, 14 bids, posted 3 h ago"* or *"PeoplePerHour project page: posted …, open until …"*.

If fewer than **15** leads survive, it runs extra Upwork skill searches (Linux/Nginx, Ansible, CloudFormation, SRE, cloud cost, DevSecOps) until it has 15 or the search budget is spent.

## Output

`runs/<stamp>/devops_leads_<stamp>.xlsx` is emailed to you after every run:

- **Leads sheet:** SR · Company Name · Position (links to the post) · Works / Description · Location · Salary / Rate (the budget) · Contract Type (Freelance - Hourly / Fixed price, Part-Time, Contract) · Contact / HR Email (or "Apply on Upwork" and similar) · Job Role · Status.
- **Details sheet:** platform, verification, links, posted date, skill match and notes.

**Contacts:**

- Posts that publish an email address get your short HTML note with your resume, at most 30 per run. Recruiters already emailed by this agent or by the Daily Job Autopilot are never emailed again.
- Marketplace projects are applied to on the platform, using the link in the sheet.

Settings live in `config.yaml`: availability, experience, freshness, `min_leads`, budget floors, search terms and caps. Keys come only from the variables that already exist (`EMAIL_ADDRESS`, `EMAIL_PASSWORD`, the SERP keys, `GEMINI/GROQ/OPENROUTER_API_KEY`, `REPORT_EMAIL`). The code is shared with `job_autopilot/` (search, screening, email template).
