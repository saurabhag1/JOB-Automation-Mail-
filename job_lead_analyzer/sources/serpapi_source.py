"""Primary source: find public hiring posts via SerpApi (Google search) and
fetch their text.

This only reads PUBLIC search results and PUBLIC pages. It never logs in to
LinkedIn, Naukri, or any portal. The genuine HR emails it surfaces are the ones
recruiters publish themselves in posts ("share your resume at ...").

Requires a SERPAPI_KEY in the environment / .env. Without it, this source
returns an empty list and the collector falls back to the free job feeds.
"""

import os

import requests
from bs4 import BeautifulSoup

import config

SERPAPI_ENDPOINT = "https://serpapi.com/search.json"

# Intent phrases that tend to appear right next to a published HR email.
_INTENT = (
    '("share your resume" OR "share your cv" OR "drop your resume" OR '
    '"interested candidates can share" OR "share their updated resume" OR '
    '"share ur cv")'
)


# Portal clause biases results toward the job portals the user cares about.
# LinkedIn posts are where recruiters publish genuine personal HR emails.
_PORTAL_CLAUSE = "(" + " OR ".join(f"site:{p}" for p in config.PORTALS) + ")"


def _build_queries(roles, locations, limit):
    """Cross roles x locations into Google queries, capped at *limit*.

    Alternates between portal-targeted queries (LinkedIn/Naukri/Instahyre/...)
    and open-web queries so we get both portal posts and other public pages.
    """
    queries = []
    toggle = True
    for loc in locations:
        for role in roles:
            if toggle:
                q = f'"{role}" {_INTENT} {_PORTAL_CLAUSE} {loc} email'
            else:
                q = f'"{role}" {_INTENT} {loc} email'
            toggle = not toggle
            queries.append(q)
            if len(queries) >= limit:
                return queries
    return queries


def _serp_search(query, api_key, num=10):
    """Run one SerpApi Google search; return list of organic results."""
    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "num": num,
        "hl": "en",
        "tbs": config.SERPAPI_RECENCY,  # restrict to recent results
    }
    try:
        resp = requests.get(params=params, url=SERPAPI_ENDPOINT, timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"  [serpapi] search failed: {exc}")
        return []
    return data.get("organic_results", []) or []


def _fetch_page_text(url):
    """Fetch a public page and return its visible text (best-effort)."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return ""
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def collect(roles=None, locations=None, max_searches=None, fetch_pages=True):
    """Return a list of raw posts: ``{url, text, source}``.

    ``text`` combines the search snippet (which often already contains the
    email) with the fetched page text, so a lead can be built even if the page
    blocks scraping.
    """
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        print("  [serpapi] SERPAPI_KEY not set -> skipping search source.")
        return []

    roles = roles or config.ROLE_KEYWORDS
    locations = locations or config.SEARCH_LOCATIONS
    max_searches = max_searches or config.MAX_SEARCHES

    posts = []
    seen_urls = set()
    for query in _build_queries(roles, locations, max_searches):
        print(f"  [serpapi] searching: {query}")
        for result in _serp_search(query, api_key):
            url = result.get("link")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            snippet = " ".join(
                str(result.get(k, "")) for k in ("title", "snippet", "snippet_highlighted_words")
            )
            page_text = _fetch_page_text(url) if fetch_pages else ""
            # Prefer the full page text for emails (snippets are truncated with
            # "...") but keep the snippet too for context / recency date.
            combined = (page_text + "\n" + snippet).strip()
            if combined:
                posts.append({
                    "url": url,
                    "text": combined,
                    "source": "serpapi",
                    "title": result.get("title", ""),
                    "date_posted": result.get("date", ""),
                })
    print(f"  [serpapi] collected {len(posts)} candidate posts.")
    return posts
