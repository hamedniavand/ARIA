"""
Scrape a directory/listing page and extract website URLs from it.
Handles two cases:
  1. Pages with direct external links (href="https://example.com")
  2. Pages with internal domain-path links (href="/websites/example.com")
     e.g. ahrefstop.com, similarweb-style directories
"""
import logging
import re
from typing import List
from urllib.parse import urlparse, urljoin

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Regex: last path segment that looks like a domain (has a dot, valid TLD length)
_DOMAIN_PATH_RE = re.compile(
    r'^/[^?#]*/([a-zA-Z0-9][a-zA-Z0-9\-]{0,61}[a-zA-Z0-9]\.[a-zA-Z]{2,})/?$'
)

# Common TLDs to confirm a path segment is a domain, not a file
_COMMON_TLDS = {
    "com","net","org","io","co","uk","de","fr","nl","au","ca","in",
    "ae","sa","eg","qa","kw","bh","om","tr","pk","ng","za","sg","ph",
    "br","mx","es","it","jp","kr","ru","pl","se","no","dk","fi","ch",
    "info","biz","tv","me","app","dev","blog","news","media","online",
    "site","web","tech","digital","agency","studio","shop","store",
}


def _looks_like_domain(segment: str) -> bool:
    """Return True if a path segment like 'example.com' looks like a domain."""
    if '.' not in segment:
        return False
    tld = segment.rsplit('.', 1)[-1].lower()
    return tld in _COMMON_TLDS


def _extract_urls_from_page(html: str, page_url: str, source_domain: str) -> List[str]:
    """
    Extract target website URLs from a directory page.
    Returns a list of 'https://domain' strings.
    """
    soup = BeautifulSoup(html, "html.parser")
    found = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith('#') or href.startswith('javascript'):
            continue

        # Case 1: direct external link
        if href.startswith("http://") or href.startswith("https://"):
            parsed = urlparse(href)
            domain = parsed.netloc.lower().lstrip("www.")
            if domain and domain != source_domain and domain not in seen:
                seen.add(domain)
                found.append(f"https://{domain}")

        # Case 2: internal path ending in a domain  e.g. /websites/example.com
        else:
            path = href.split('?')[0].split('#')[0]
            segments = [s for s in path.strip('/').split('/') if s]
            if segments:
                last = segments[-1]
                if _looks_like_domain(last) and last not in seen:
                    seen.add(last)
                    found.append(f"https://{last}")

    return found


def _next_page_url(current_url: str, soup, visited: set) -> str | None:
    """
    Determine the next page URL.
    Tries: rel=next link → text-based next link → numeric URL increment.
    """
    # rel=next or text-based next link
    next_link = (
        soup.find("a", rel="next") or
        soup.find("a", string=lambda t: t and t.strip().lower() in ("next", "next »", "›", "»", "next page"))
    )
    if next_link and next_link.get("href"):
        candidate = urljoin(current_url, next_link["href"])
        if candidate not in visited:
            return candidate

    # Numeric increment: /websites/10 → /websites/11
    parsed = urlparse(current_url)
    path_parts = parsed.path.rstrip('/').rsplit('/', 1)
    if len(path_parts) == 2 and path_parts[1].isdigit():
        next_num = int(path_parts[1]) + 1
        next_path = f"{path_parts[0]}/{next_num}"
        candidate = parsed._replace(path=next_path, query='').geturl()
        if candidate not in visited:
            return candidate

    return None


async def scrape_directory_page(url: str, max_pages: int = 10) -> List[str]:
    """
    Visit a directory/listing page and return all website URLs found.
    Follows pagination up to max_pages.
    """
    source_domain = urlparse(url).netloc.lower().lstrip("www.")
    collected: List[str] = []
    seen_domains: set = set()
    visited: set = set()
    current_url = url

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
        for page_num in range(max_pages):
            if current_url in visited:
                break
            visited.add(current_url)

            try:
                resp = await client.get(current_url)
                if resp.status_code not in (200, 202):
                    logger.warning("Directory page %s returned %d", current_url, resp.status_code)
                    break
            except Exception as exc:
                logger.warning("Directory fetch error %s: %s", current_url, exc)
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            page_urls = _extract_urls_from_page(resp.text, current_url, source_domain)

            new_on_page = 0
            for u in page_urls:
                domain = urlparse(u).netloc.lower().lstrip("www.")
                if domain and domain not in seen_domains:
                    seen_domains.add(domain)
                    collected.append(u)
                    new_on_page += 1

            logger.info("Directory page %d '%s': %d new URLs (total %d)",
                        page_num + 1, current_url, new_on_page, len(collected))

            # Stop paginating if this page added nothing
            if new_on_page == 0 and page_num > 0:
                break

            next_url = _next_page_url(current_url, soup, visited)
            if not next_url:
                break
            current_url = next_url

    logger.info("Directory scrape '%s': %d total unique URLs across %d pages",
                url, len(collected), len(visited))
    return collected
