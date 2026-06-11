# Job Lead Analyzer

A daily tool that scrapes **public** job posts, extracts **genuine, reply-capable HR
emails** for **DevOps / AWS / Cloud** roles, filters by your experience range and skills,
de-duplicates against previous runs, and writes a JSON file of fresh, unique leads.

It surfaces the emails recruiters publish themselves in hiring posts
("share your resume at …"). It **never** logs in to or scrapes behind the login wall of
LinkedIn, Naukri, or any portal — only public search results and public pages/feeds.

## What you get

`daily_job_leads.json` — a short, clean record per lead:

| field | meaning |
|-------|---------|
| `company` | company name (best-effort) |
| `position` | role (DevOps Engineer, AWS Engineer, …) |
| `email` | best genuine HR email — a complete, named-person address preferred |
| `alternateEmails` | other reply-capable emails from the same post |
| `emailType` | `personal` or `generic` (never `no-reply` / fraud / boilerplate) |
| `phone` | phone number if the post listed one |
| `location` | up to 3 matched locations, Remote prioritized |
| `experience` | e.g. `1-3 yrs`, `3+ yrs`, `Not specified` |
| `skills` | which of your skills the post actually mentions (whole-word) |
| `position` | the real job title from the post |
| `datePosted`, `source`, `url` | recency + where it came from |

**Quality rules baked in:**
- Truncated emails (Google snippets cut off as `abhishe...@x.com`) are rejected — if no
  complete, reply-capable email is found, that job is **skipped**.
- A post is kept only if it mentions a **core** DevOps/Cloud skill (`config.CORE_SKILLS`)
  plus at least `MIN_SKILL_MATCHES` of your skills — so unrelated jobs are dropped.
- Career pages / Naukri / Indeed / Glassdoor listings use Apply forms and rarely publish a
  person's email; with the email-only rule those jobs are skipped (no address to send to).
  Genuine personal HR emails come from recruiter posts ("share your resume at …").

## Install

```bash
cd job_lead_analyzer
python3 -m pip install -r requirements.txt
```

## Get the most genuine HR emails (recommended)

Real personal HR emails come from Google search. The tool can query **five SERP
providers** — each returns Google organic results and is asked for its own slice
of leads, so they surface *different* jobs/emails. All five have a **free tier**;
you don't need all of them — any provider whose key is missing is skipped and the
rest still run (graceful fallback).

| provider | env var | free tier | where to get the key |
|----------|---------|-----------|----------------------|
| SerpApi | `SERPAPI_KEY` | 250 searches/month | https://serpapi.com/manage-api-key |
| Serper.dev | `SERPER_KEY` | 2,500 credits (no card) | https://serper.dev → top-right → API Key |
| ScraperAPI | `SCRAPERAPI_KEY` | 5,000 credits | https://www.scraperapi.com → dashboard |
| Scrapingdog | `SCRAPINGDOG_KEY` | 1,000 credits (~200 searches) | https://www.scrapingdog.com/google-serp-api |
| SearchApi.io | `SEARCHAPI_KEY` | 100 searches | https://www.searchapi.io → API Key |

```bash
cp .env.example .env
# edit .env and fill in the keys you have (any subset works)
```

> **How the count works:** `--per-provider 5` keeps up to 5 unique leads from each
> provider; with all 5 that's up to **25** unique HR emails per run
> (`--target-count 25`). Emails are de-duplicated globally — across providers *and*
> previous runs (`state.json`) — so every lead is a distinct, fresh address.
>
> **Credit budget:** `--max-searches` is searches **per provider per run** (each = 1
> credit; Scrapingdog charges 5). Keep it ~3 for daily use. The tool searches
> LinkedIn, Naukri, Indeed, Glassdoor, Instahyre, Hirect and the open web,
> restricted to recent posts.

## Run (every day)

```bash
python3 collector.py                        # 5 providers x 5 = up to 25 unique leads
python3 collector.py --providers serper,searchapi   # only specific providers
```

No key yet? Run on free job feeds only (lower yield, no personal emails guaranteed):

```bash
python3 collector.py --no-serpapi           # alias: --no-serp (skip all SERP providers)
```

Each run writes new emails to `state.json`, so the **next day you get different leads**.

## Useful options

```bash
python3 collector.py \
  --target-count 25 \                       # total unique leads to keep
  --per-provider 5 \                        # max unique leads per provider
  --providers serpapi,serper,scraperapi,scrapingdog,searchapi \
  --exp-min 0 --exp-max 5 \                 # your experience window (default 0–5)
  --days 4 \                                # only posts from the last N days
  --locations "remote,pune,mumbai,singapore,uae,vietnam" \
  --max-searches 8 \                        # credits to spend per provider this run
  --personal-only \                         # drop generic hr@/careers@ fallback
  --no-dedupe                               # ignore state.json (testing)
```

## Customize

Edit `config.py`:
- `CANDIDATE_SKILLS` — your skills (used to match & score jobs)
- `ROLE_KEYWORDS` — roles to hunt for
- `LOCATION_PRIORITY` / `SEARCH_LOCATIONS` — where to look, ranked
- `EXPERIENCE_MIN` / `EXPERIENCE_MAX`, `TARGET_COUNT` — defaults

## Schedule it daily (macOS/Linux cron example)

```cron
# 9 AM every weekday
0 9 * * 1-5 cd /Users/saurabhagarwal/Desktop/JOB-Automation-Mail-/job_lead_analyzer && /usr/bin/python3 collector.py --target-count 10 >> run.log 2>&1
```

## Send your resume to the leads

`send_resume.py` reads `daily_job_leads.json` and emails your resume + template to each
lead. It reuses the template and resume from `../bulk_mail_sender/`.

```bash
python3 send_resume.py --dry-run            # preview who would be emailed
python3 send_resume.py --max-send 10        # actually send (needs Gmail creds)
```

Credentials come from the environment / `.env`:

```env
EMAIL_ADDRESS=you@gmail.com
EMAIL_PASSWORD=your_gmail_app_password      # an App Password, not your login password
```

## Run it daily on GitHub Actions (scrape → send)

The workflow `.github/workflows/daily-job-scrape-send.yml` runs **after** your existing
"Manual Bulk Mail Sender" workflow finishes: it collects fresh leads, emails the resume,
and commits `state.json` back so the next day's emails are new and unique.

Add these repository **secrets** (Settings → Secrets and variables → Actions):

| secret | what |
|--------|------|
| `SERPAPI_KEY` | your SerpApi key |
| `EMAIL_ADDRESS` | sending Gmail address |
| `EMAIL_PASSWORD` | Gmail **App Password** |

You can also trigger it manually from the Actions tab (with a **dry-run** checkbox to
preview without sending). Keep `max_searches` low (~3) to stay within the SerpApi free tier.

## Notes

- Genuine personal HR emails only exist where a recruiter published them publicly, so daily
  yield depends on what's posted and on having a SerpApi key.
- Cold-emailing scraped addresses at volume can hurt your Gmail's sending reputation; the
  sender rate-limits and caps per run. Keep volumes modest.
