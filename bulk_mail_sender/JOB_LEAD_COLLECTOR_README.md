# Daily Job Lead Collector

This is a separate workflow from `send_mail.py`. It searches public web pages for recent job openings, extracts HR or hiring-team emails, writes a JSON file, dedupes against previous runs, and can optionally send your template/resume email.

## Install

```powershell
python -m pip install -r requirements.txt
```

## Collect 50 New Leads

```powershell
python .\daily_job_lead_collector.py --target-count 50 --max-age-days 7
```

If search engines block scraping or it is taking too long, use online-feed-only mode. This still reads from public online job feeds and does not use local pasted data:

```powershell
python .\daily_job_lead_collector.py --target-count 50 --max-age-days 7 --skip-live-search
```

To include marked generic hiring inboxes such as `careers@company.com` when a company domain is discovered online but no visible email is published, add:

```powershell
python .\daily_job_lead_collector.py --target-count 50 --max-age-days 7 --include-generic-emails
```

Outputs:

- `daily_job_leads.json`
- `daily_job_leads.csv`
- `daily_job_leads_state.json`

The state file prevents the same email from being used again in later runs.

## Collect And Send

```powershell
python .\daily_job_lead_collector.py --target-count 50 --max-age-days 7 --send
```

To preview recipients without sending:

```powershell
python .\daily_job_lead_collector.py --send-only --dry-run-send
```

## Add Skills Or Locations

You can edit `DEFAULT_SKILLS` and `DEFAULT_LOCATIONS` inside `daily_job_lead_collector.py`, or pass values from the command line:

```powershell
python .\daily_job_lead_collector.py --skills "DevOps Engineer,AWS,Kubernetes,Terraform" --locations "Pune,Mumbai,Nagpur,Remote India"
```

## Run With PowerShell Automation

```powershell
powershell -ExecutionPolicy Bypass -File .\run_daily_job_leads.ps1
```

Online-feed-only mode:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_daily_job_leads.ps1 -Fast
```

Collect and send:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_daily_job_leads.ps1 -Send
```

Schedule this command in Windows Task Scheduler for Monday-Friday. The Python script also skips Saturday/Sunday unless you pass `--force` or `-Force`.

## Better Search Results

By default the script uses public online job feeds plus public DuckDuckGo/Bing search. Search engines can block automated scraping. For more stable Google-like results, add a SerpApi key to `.env`:

```env
SERPAPI_KEY=your_key_here
```

Then run:

```powershell
python .\daily_job_lead_collector.py --search-provider serpapi
```

The script only reads public pages. It does not log in to LinkedIn, Naukri, Google Jobs, or other portals.
