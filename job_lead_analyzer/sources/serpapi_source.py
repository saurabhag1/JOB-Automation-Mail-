"""Backward-compatible shim -- SerpApi is now one of several SERP providers.

The multi-provider implementation lives in ``serp_providers``. This module is
kept so existing imports (``from sources import serpapi_source``) and any
single-provider callers keep working; it simply runs the ``serpapi`` provider.
"""

from sources import serp_providers


def collect(roles=None, locations=None, max_searches=None, fetch_pages=True):
    """Collect raw posts using only the SerpApi provider (legacy entry point)."""
    return serp_providers.collect(
        providers=["serpapi"],
        roles=roles,
        locations=locations,
        max_searches=max_searches,
        fetch_pages=fetch_pages,
    )
