"""AI-driven search for recruiter posts with HR emails, round after round.

Round 1 runs the planned searches. After each round the free LLMs (Gemini ->
Groq -> OpenRouter) look at the posts that actually had an HR email and write
new search phrases in the same vein (other titles, cities, wording such as
"immediate joiner" / "C2H" / "walk-in"). Rounds continue until one finds no new
HR email, the round limit is reached or the search budget is spent.

Two search engines, either or both:
  google  the SERP keys (Serper, ScraperAPI, SerpApi, Scrapingdog, SearchApi), past week only
  web     OpenRouter's web search (Exa index, strong on LinkedIn posts); costs about $0.007 per
          search from your OpenRouter credit and uses one of its 50 free requests a day

Nothing an LLM *says* about a job is trusted. HR emails, titles and companies come
from the post's own text (the search engine's copy and the fetched page); an LLM's
reading of a post is only used where its words appear in that text.
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from autopilot.config import Settings
from autopilot.discovery import google_search as gs
from autopilot.discovery.common import session
from autopilot.models import Job

RECRUITER_SITES = "(site:linkedin.com/posts OR site:linkedin.com/feed)"
_YEARS = re.compile(r"(\d{1,2})\s*(?:\+|-|–|to)?\s*(?:\d{1,2})?\s*\+?\s*(?:years?|yrs?)", re.I)


# --------------------------------------------------------------------------- #
# OpenRouter web search
# --------------------------------------------------------------------------- #
def web_search(query: str, settings: Settings, sess) -> list[dict]:
    """Search results as {link, title, snippet, date}, taken from the search engine's citations
    (the real page text), never from the model's reply."""
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return []
    model = settings.get("sources.ai_search.web_model", "nvidia/nemotron-3-super-120b-a12b:free")
    since = (datetime.now(timezone.utc) - timedelta(days=int(settings.get("search.max_age_days", 7)))).date()
    r = sess.post("https://openrouter.ai/api/v1/chat/completions", timeout=120,
                  headers={"Authorization": f"Bearer {key}", "X-Title": "Job Autopilot"},
                  json={"model": model, "max_tokens": 16,
                        "plugins": [{"id": "web", "max_results": int(settings.get("sources.ai_search.results", 10))}],
                        "messages": [{"role": "user",
                                      "content": f"{query} {since:%B %Y} (posted after {since.isoformat()})"}]})
    r.raise_for_status()
    msg = ((r.json().get("choices") or [{}])[0].get("message")) or {}
    out = []
    for a in msg.get("annotations") or []:
        cite = a.get("url_citation") or {}
        content = cite.get("content") or ""
        if not cite.get("url"):
            continue
        dated = re.search(r"\b(20\d\d-\d\d-\d\d)\b", content)
        out.append({"link": cite["url"], "title": cite.get("title") or content.split("\n", 1)[0].lstrip("# ")[:150],
                    "snippet": content[:6000], "date": dated.group(1) if dated else ""})
    return out


# --------------------------------------------------------------------------- #
# LLM help: new search phrases, and reading posts
# --------------------------------------------------------------------------- #
def _clean_phrase(q) -> str:
    q = re.sub(r"\s+", " ", str(q or "")).strip().strip('"')
    q = re.sub(r"\bsite:\S+", "", q).strip()          # sites are added per engine
    return q if 8 <= len(q) <= 110 and not re.search(r"[{}<>]", q) else ""


def expand_queries(llm, roles: list[str], places: list[str], years: int, used: list[str],
                   examples: list[str], n: int) -> list[str]:
    """New search phrases in the style of the ones that found HR emails."""
    if not llm or n <= 0:
        return []
    prompt = (
        "You help a job seeker find recruiter posts (mostly LinkedIn posts) that ask candidates to email their "
        f"resume. The candidate: {years} years as a DevOps / Cloud engineer (AWS, Kubernetes, Docker, CI/CD, "
        f"Terraform, monitoring), wants roles in India ({', '.join(places)}) or remote.\n"
        f"Target roles: {', '.join(roles)}.\n\n"
        "Searches already done (do not repeat them or near-copies):\n- " + "\n- ".join(used[-40:]) + "\n\n"
        "Posts that DID have an HR email (learn their wording, titles and places):\n- "
        + ("\n- ".join(examples[:12]) or "(none yet)") + "\n\n"
        f"Write {n} NEW short web-search phrases (4-10 words each), each likely to find a different recent "
        "recruiter post for this candidate. Vary role synonyms, cities, seniority words (2-4 years, 3+ years, "
        "mid-level), hiring wording (hiring, immediate joiners, share CV, C2H, contract, walk-in) and tools. "
        'No quotes, no site: operators. Reply as JSON: {"queries": ["...", "..."]}')
    data = llm.json(prompt) or {}
    seen = {u.lower() for u in used}
    out = []
    for q in data.get("queries") or []:
        q = _clean_phrase(q)
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out[:n]


def _words_in(phrase: str, text: str, share: float = 0.75) -> bool:
    words = [w for w in re.findall(r"[a-z0-9+#]+", (phrase or "").lower()) if len(w) > 1]
    low = text.lower()
    return bool(words) and sum(w in low for w in words) / len(words) >= share


def read_posts(llm, jobs: list[Job], log=print) -> int:
    """Recruiter posts carry the searched role as their title. Let an LLM read each post for
    the real role, company and experience asked - and keep only what the post's own text says."""
    posts = [j for j in jobs if j.hr_email and j.description][:40]
    if not llm or not posts:
        return 0
    fixed = 0
    for start in range(0, len(posts), 8):
        batch = posts[start:start + 8]
        listing = "\n\n".join(f"[{i}] {j.description[:1800]}" for i, j in enumerate(batch))
        data = llm.json(
            "Each block below is a job post. For each, extract what the post itself states. Reply as JSON "
            '{"posts": [{"i": 0, "title": "role being hired for, as written", "company": "hiring company if '
            'named, else empty", "experience": "years asked, as written, else empty", "hiring": true}]}. '
            '"hiring" is false for training-course ads, job-seeker posts and posts listing many unrelated roles.'
            "\n\n" + listing) or {}
        for item in data.get("posts") or []:
            try:
                job = batch[int(item.get("i"))]
            except (TypeError, ValueError, IndexError):
                continue
            text = job.description
            if item.get("hiring") is False:
                job.flags.append("not a hiring post (AI read)")
                job.title = f"{job.title} [not a hiring post]"
                fixed += 1
                continue
            title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
            if title and len(title) <= 90 and _words_in(title, text):
                job.title = title
                fixed += 1
            company = str(item.get("company") or "").strip()
            if company and len(company) <= 60 and company.lower() in text.lower():
                job.company = company
            exp = str(item.get("experience") or "").strip()
            if exp and _YEARS.search(exp) and _words_in(exp, text, 1.0):
                job.experience_text = exp
    log(f"  [ai] read {len(posts)} recruiter posts: {fixed} titles/flags taken from the post text")
    return fixed


# --------------------------------------------------------------------------- #
# The rounds
# --------------------------------------------------------------------------- #
def _google_query(phrase: str) -> str:
    return f"{phrase} {gs.SHARE} {RECRUITER_SITES}"


def _first_queries(settings: Settings, roles: list[str], places: list[str], n: int) -> list[str]:
    out = []
    for i in range(n):
        role, place = roles[i % len(roles)], places[(i // len(roles)) % len(places)]
        out.append(f"hiring {role} {place} share resume email")
    return out


def collect(settings: Settings, log=print) -> list[Job]:
    from autopilot.tailoring.free_llm import QuickLLM

    cfg = "sources.ai_search"
    providers = gs.available_providers()
    google_budget = int(settings.get("sources.google_search.max_searches", 20)) if providers else 0
    web_budget = int(settings.get(f"{cfg}.web_searches", 15)) if os.getenv("OPENROUTER_API_KEY") else 0
    max_rounds = int(settings.get(f"{cfg}.max_rounds", 4))
    roles = settings.get("search.keywords", ["DevOps Engineer"])
    places = ["India", "Remote"] + list(settings.get("locations.india.cities", []))
    years = int(settings.get("search.experience_years", 3))
    llm = QuickLLM(settings, log)
    if not google_budget and not web_budget:
        log("  [ai] no SERP keys and no OPENROUTER_API_KEY - AI search skipped")
        return []
    if not llm:
        log("  [ai] no LLM key: one round of planned searches, no AI-written follow-ups")

    sess = session()
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(settings.get("search.max_age_days", 7)))
    jobs: list[Job] = []
    seen_urls: set[str] = set()
    hr_seen: set[str] = set()
    used: list[str] = []
    examples: list[str] = []
    g_left, w_left = google_budget, web_budget

    # Round 1: the planned portal searches (half the Google budget) + recruiter-post phrases on the web.
    # Later rounds: AI-written phrases, each searched on the web and on Google while budget lasts.
    plan = gs.plan_queries(settings)[:google_budget // 2]
    phrases = _first_queries(settings, roles, places, max(2, web_budget // 3) if web_budget else 3)
    counter = iter(range(10 ** 6))
    for rnd in range(1, max_rounds + 1):
        tasks = []   # (engine, group, place, role, query)
        if rnd == 1:
            tasks += [("google", g, place, re.match(r'"([^"]+)"', q).group(1), q) for g, place, q in plan]
        for phrase in phrases:
            role = next((r for r in roles if r.lower() in phrase.lower()), roles[0])
            place = next((p for p in places if p.lower() in phrase.lower()), "India")
            if w_left > 0:
                tasks.append(("web", "recruiter_posts", place, role, phrase))
                w_left -= 1
            # Round 1's Google budget went to the portal plan; later rounds share it with the web.
            if g_left - len(plan) * (rnd == 1) > 0 and (rnd > 1 or not web_budget):
                tasks.append(("google", "recruiter_posts", place, role, _google_query(phrase)))
                g_left -= 1
        if rnd == 1:
            g_left -= len(plan)
        used += phrases
        if not tasks:
            break

        def run(task):
            engine, _, _, _, query = task
            try:
                if engine == "web":
                    return task, web_search(query, settings, sess)
                provider = providers[next(counter) % len(providers)]
                return task, gs.SEARCH[provider](sess, query, os.environ[gs.KEY_ENV[provider]])
            except Exception as exc:  # noqa: BLE001 - one failed search never stops the round
                # Never print the exception text: request URLs carry API keys.
                status = getattr(getattr(exc, "response", None), "status_code", "")
                where = "openrouter" if engine == "web" else "SERP provider"
                log(f"  [ai:{engine}] {where} search failed: {type(exc).__name__} {status}".rstrip())
                return task, []

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(run, tasks))
        pending, stale = [], 0
        for (engine, group, place, role, _), items in results:
            for item in items:
                url = item.get("link") or item.get("url")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                posted = gs.linkedin_post_date(url) or gs._age(str(item.get("date") or ""))
                if posted and posted < cutoff:
                    stale += 1          # older than this week: not worth fetching
                    continue
                pending.append((engine, group, place, role, item))
        with ThreadPoolExecutor(max_workers=8) as pool:
            texts = list(pool.map(lambda p: gs._page_text(sess, p[4].get("link") or p[4].get("url")), pending))
        new_hr = 0
        for (engine, group, place, role, item), text in zip(pending, texts):
            job = gs._to_job(group, place, role, item, text)
            if job is None:
                continue
            if engine == "web":
                job.source = "ai-web:" + job.source.split(":", 1)[-1]
                # Web search is not limited to the past week: an undated result is not trusted as fresh.
                job.date_trusted = job.date_posted is not None
            jobs.append(job)
            if job.hr_email and job.hr_email.lower() not in hr_seen:
                hr_seen.add(job.hr_email.lower())
                new_hr += 1
                examples.append(f"{str(item.get('title', ''))[:120]} | {job.location}")
        log(f"  [ai] round {rnd}: {sum(t[0] == 'web' for t in tasks)} web + {sum(t[0] == 'google' for t in tasks)} "
            f"Google searches -> {len(pending)} new pages ({stale} older than this week skipped), "
            f"{new_hr} new HR emails")
        if new_hr == 0 and rnd > 1:
            log("  [ai] a round found no new HR email - stopping")
            break
        if rnd == max_rounds or (w_left <= 0 and g_left <= 0):
            break
        phrases = expand_queries(llm, roles, places, years, used, examples, max(w_left, min(g_left, 6)))
        if not phrases:
            break

    read_posts(llm, jobs, log)
    dropped = [j for j in jobs if j.title.endswith("[not a hiring post]")]
    if dropped:
        log(f"  [ai] dropped {len(dropped)} posts that are not job openings (training ads, job seekers, role lists)")
    jobs = [j for j in jobs if j not in dropped]
    if llm.calls:
        log(f"  [ai] LLM calls: {', '.join(f'{k} x{v}' for k, v in llm.calls.items())}")
    log(f"  [ai] {len(jobs)} posts, {sum(1 for j in jobs if j.hr_email)} with an HR email "
        f"(budget left: {max(w_left, 0)} web, {max(g_left, 0)} Google)")
    return jobs

