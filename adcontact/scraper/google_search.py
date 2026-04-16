"""
Search backend — Serper.dev (Google Search API).
Free: 2,500 queries on signup, no credit card required.
Setup: https://serper.dev → sign up → copy API key → SERPER_API_KEY in .env
"""
import logging
import os
import asyncio
import random
from typing import List
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")

QUERY_TEMPLATES = [
    '{niche} "advertise with us"',
    '{niche} "advertise here"',
    '{niche} "media kit" contact email',
    '{niche} "sponsored post" OR "sponsored content" email',
    '{niche} blog advertising contact email',
    '{niche} website "partner with us" OR "ad rates"',
]


def build_queries(niche: str) -> List[str]:
    return [t.replace("{niche}", niche.strip()) for t in QUERY_TEMPLATES]


async def _search_serper(client: httpx.AsyncClient, query: str) -> List[str]:
    """Query Serper.dev (Google results) and return result URLs."""
    try:
        resp = await client.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": 10},
            headers={
                "X-API-KEY": SERPER_API_KEY,
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        if resp.status_code == 429:
            logger.warning("Serper rate limit — sleeping 10s")
            await asyncio.sleep(10)
            return []
        if resp.status_code != 200:
            logger.warning("Serper failed (%d): %s", resp.status_code, query)
            return []
        data = resp.json()
        urls = [r["link"] for r in data.get("organic", []) if "link" in r]
        logger.info("Serper '%s': %d results", query[:60], len(urls))
        _increment_serper_counter()
        return urls
    except Exception as exc:
        logger.warning("Serper error: %s", exc)
        return []


def _increment_serper_counter() -> None:
    """Increment the shared Serper query counter (synced with ARIA backend)."""
    try:
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))
        import serper_counter
        serper_counter.increment()
    except Exception as exc:
        logger.debug("Serper counter update failed: %s", exc)


async def collect_urls(niche: str) -> List[str]:
    """Run all query templates for a niche and return deduplicated URLs."""
    if not SERPER_API_KEY:
        logger.error("SERPER_API_KEY not set — niche search disabled. Add to .env and restart.")
        return []

    queries = build_queries(niche)
    all_urls: List[str] = []

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for query in queries:
            urls = await _search_serper(client, query)
            all_urls.extend(urls)
            await asyncio.sleep(0.5 + random.uniform(0, 0.5))

    # Deduplicate by normalised domain
    seen_domains: set = set()
    unique: List[str] = []
    for url in all_urls:
        try:
            domain = urlparse(url).netloc.lower().lstrip("www.")
            if domain and domain not in seen_domains:
                seen_domains.add(domain)
                unique.append(url)
        except Exception:
            pass

    logger.info("collect_urls '%s': %d unique domains from %d raw results", niche, len(unique), len(all_urls))
    return unique
