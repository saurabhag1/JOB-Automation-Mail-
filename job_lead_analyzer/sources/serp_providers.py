"""Multi-provider Google SERP scraping for genuine HR-email job posts.

Every provider here does the same job your original SerpApi source did: run a
Google search for public hiring posts ("share your resume at ..."), read the
*organic results* JSON, then fetch each public page so we can extract the HR
email. Only the request shape and the response key differ per provider, so we
normalize them all to one shape and reuse the same query-building and
page-fetch logic.

Supported free providers (all return Google organic results):

    serpapi      SERPAPI_KEY       250 searches/month   https://serpapi.com
    serper       SERPER_KEY        2,500 credits        https://serper.dev
    scraperapi   SCRAPERAPI_KEY    5,000 credits        https://www.scraperapi.com
    scrapingdog  SCRAPINGDOG_KEY   1,000 credits        https://www.scrapingdog.com
    searchapi    SEARCHAPI_KEY     100 searches         https://www.searchapi.io

Fallback is graceful: a provider with no key, an API error, or an empty
response is logged and skipped -- the remaining providers still run, so one
dead provider never stops the others.

This only reads PUBLIC search results and PUBLIC pages. It never logs in to
LinkedIn, Naukri, or any portal.
"""

import os

import requests
from bs4 import BeautifulSoup

import config

# Intent phrases that tend to appear right next to a published HR email.
_INTENT = (
    '("share your resume" OR "share your cv" OR "drop your resume" OR '
    '"interested candidates can share" OR "share their updated resume" OR '
    '"share ur cv")'
)

# Portal clause biases results toward the job portals the user cares about.
_PORTAL_CLAUSE = "(" + " OR ".join(f"site:{p}" for p in config.PORTALS) + ")"


def _build_queries(roles, locations, limit):
    """Cross roles x locations into Google queries, capped at *limit*.

    Walks roles and locations *diagonally* (role[i], location[i] together) so
    the first few queries each hit a DIFFERENT role AND a DIFFERENT location.
    This matters because daily runs use a small --max-searches (e.g. 3): a
    naive locations-outer loop would spend all 3 queries on the first location
    ("remote") and never search Pune/Mumbai/Bangalore/India. The diagonal walk
    guarantees location + role breadth even with a tiny search budget.

    Alternates between portal-targeted queries (LinkedIn/Naukri/...) and
    open-web queries so we get both portal posts and other public pages.
    """
    if not roles or not locations:
        return []
    # Diagonal enumeration of every (role, location) combo: each "offset" pass
    # pairs role[i] with location[i+offset], so consecutive combos differ in
    # BOTH role and location. Dedupe preserves this diversity-first ordering.
    ordered, seen = [], set()
    for offset in range(len(locations)):
        for ri, role in enumerate(roles):
            loc = locations[(ri + offset) % len(locations)]
            if (role, loc) not in seen:
                seen.add((role, loc))
                ordered.append((role, loc))

    queries = []
    for role, loc in ordered[:limit]:
        if len(queries) % 2 == 0:
            q = f'"{role}" {_INTENT} {_PORTAL_CLAUSE} {loc} email'
        else:
            q = f'"{role}" {_INTENT} {loc} email'
        queries.append(q)
    return queries


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


# --------------------------------------------------------------------------- #
# Provider adapters. Each returns a list of normalized organic results:
#   {"link": str, "title": str, "snippet": str, "date": str}
# Raising requests.RequestException / ValueError is fine -- collect_provider
# catches it and falls through to the next query / provider.
# --------------------------------------------------------------------------- #

def _normalize(items, link_key="link"):
    out = []
    for r in items or []:
        link = r.get(link_key) or r.get("link") or r.get("url")
        if not link:
            continue
        snippet = r.get("snippet", "")
        highlighted = r.get("snippet_highlighted_words") or r.get("snippet_highlighted") or []
        if isinstance(highlighted, list):
            highlighted = " ".join(str(w) for w in highlighted)
        out.append({
            "link": link,
            "title": str(r.get("title", "")),
            "snippet": f"{snippet} {highlighted}".strip(),
            "date": str(r.get("date", "") or r.get("date_utc", "")),
        })
    return out


def _serpapi_search(query, api_key, num):
    """SerpApi -- GET, organic_results."""
    resp = requests.get(
        "https://serpapi.com/search.json",
        params={"engine": "google", "q": query, "api_key": api_key,
                "num": num, "hl": "en", "tbs": config.SERPAPI_RECENCY},
        timeout=config.REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return _normalize(resp.json().get("organic_results", []))


def _serper_search(query, api_key, num):
    """Serper.dev -- POST with X-API-KEY header, results under 'organic'."""
    resp = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": query, "num": num, "gl": "us", "hl": "en",
              "tbs": config.SERPAPI_RECENCY},
        timeout=config.REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return _normalize(resp.json().get("organic", []))


def _scraperapi_search(query, api_key, num):
    """ScraperAPI structured Google endpoint -- GET, organic_results."""
    resp = requests.get(
        "https://api.scraperapi.com/structured/google/search",
        params={"api_key": api_key, "query": query, "num": num, "country_code": "us"},
        timeout=config.REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return _normalize(resp.json().get("organic_results", []))


def _scrapingdog_search(query, api_key, num):
    """Scrapingdog Google SERP API -- GET, organic_results (5 credits/search)."""
    resp = requests.get(
        "https://api.scrapingdog.com/google",
        params={"api_key": api_key, "query": query, "results": num,
                "country": "us", "page": 0},
        timeout=config.REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return _normalize(resp.json().get("organic_results", []))


def _searchapi_search(query, api_key, num):
    """SearchApi.io -- GET, SerpApi-identical organic_results."""
    resp = requests.get(
        "https://www.searchapi.io/api/v1/search",
        params={"engine": "google", "q": query, "api_key": api_key,
                "num": num, "hl": "en", "tbs": config.SERPAPI_RECENCY},
        timeout=config.REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return _normalize(resp.json().get("organic_results", []))


# Registry: provider name -> {env var holding its key, search function}.
PROVIDERS = {
    "serpapi": {"env": "SERPAPI_KEY", "search": _serpapi_search},
    "serper": {"env": "SERPER_KEY", "search": _serper_search},
    "scraperapi": {"env": "SCRAPERAPI_KEY", "search": _scraperapi_search},
    "scrapingdog": {"env": "SCRAPINGDOG_KEY", "search": _scrapingdog_search},
    "searchapi": {"env": "SEARCHAPI_KEY", "search": _searchapi_search},
}


def collect_provider(name, roles, locations, max_searches, fetch_pages=True, seen_urls=None):
    """Run one provider's searches and return raw posts tagged with its name.

    Returns ``[]`` (never raises) if the provider has no key or every search
    fails -- that's the fallback contract the collector relies on.
    """
    cfg = PROVIDERS.get(name)
    if cfg is None:
        print(f"  [{name}] unknown provider -> skipping.")
        return []
    api_key = os.getenv(cfg["env"])
    if not api_key:
        print(f"  [{name}] {cfg['env']} not set -> skipping (other providers continue).")
        return []

    seen_urls = seen_urls if seen_urls is not None else set()
    posts = []
    for query in _build_queries(roles, locations, max_searches):
        print(f"  [{name}] searching: {query}")
        try:
            results = cfg["search"](query, api_key, 10)
        except (requests.RequestException, ValueError) as exc:
            print(f"  [{name}] search failed ({exc}) -> next query.")
            continue
        for result in results:
            url = result["link"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            snippet = f"{result['title']} {result['snippet']}".strip()
            page_text = _fetch_page_text(url) if fetch_pages else ""
            combined = (page_text + "\n" + snippet).strip()
            if combined:
                posts.append({
                    "url": url,
                    "text": combined,
                    "source": name,
                    "title": result["title"],
                    "date_posted": result["date"],
                })
    print(f"  [{name}] collected {len(posts)} candidate posts.")
    return posts


def collect(providers=None, roles=None, locations=None, max_searches=None, fetch_pages=True):
    """Collect raw posts from every configured provider (with fallback).

    ``providers`` defaults to ``config.SERP_PROVIDERS``. URLs are de-duplicated
    across providers so the same page isn't fetched twice, which means each
    provider naturally contributes *different* jobs/emails.
    """
    providers = providers or config.SERP_PROVIDERS
    roles = roles or config.ROLE_KEYWORDS
    locations = locations or config.SEARCH_LOCATIONS
    max_searches = max_searches or config.MAX_SEARCHES

    posts = []
    seen_urls = set()
    for name in providers:
        posts.extend(collect_provider(
            name, roles, locations, max_searches,
            fetch_pages=fetch_pages, seen_urls=seen_urls,
        ))
    return posts
