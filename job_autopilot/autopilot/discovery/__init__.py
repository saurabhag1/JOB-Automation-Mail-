"""Collect raw jobs from every enabled source. A failing source is logged and
skipped - one dead site never stops the others."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from autopilot.config import Settings
from autopilot.models import Job


def discover(settings: Settings, log=print) -> list[Job]:
    from autopilot.discovery import ai_search, ats_boards, boards, google_search, local_files, remote_feeds

    tasks = {}
    for site in boards.SITES:
        if settings.get(f"sources.{site}.enabled", False):
            tasks[site] = lambda site=site: boards.collect(site, settings, log)
    if settings.get("sources.ats_boards.enabled", False):
        tasks["ats"] = lambda: ats_boards.collect(settings, log)
    if settings.get("sources.ai_search.enabled", False):
        # Google + OpenRouter web search in rounds, with AI-written follow-up searches.
        tasks["ai-search"] = lambda: ai_search.collect(settings, log)
    elif settings.get("sources.google_search.enabled", False):
        tasks["google"] = lambda: google_search.collect(settings, log)
    if settings.get("sources.remote_feeds.enabled", False):
        tasks["remote-feeds"] = lambda: remote_feeds.collect(settings, log)
    if settings.get("sources.hr_email_leads.enabled", False):
        tasks["hr-leads"] = lambda: local_files.hr_email_leads(settings, log)
    if settings.get("sources.pasted_jobs.enabled", False):
        tasks["pasted"] = lambda: local_files.pasted_jobs(settings, log)

    jobs: list[Job] = []
    # Different sites in parallel; each site paces its own requests.
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {name: pool.submit(fn) for name, fn in tasks.items()}
        for name, fut in futures.items():
            try:
                found = fut.result()
                log(f"  [{name}] {len(found)} postings")
                jobs.extend(found)
            except Exception as exc:  # noqa: BLE001 - isolate source failures
                log(f"  [{name}] failed: {type(exc).__name__}: {exc}")
    return jobs
