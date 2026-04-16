"""Visit a website, find contact/advertise pages, extract emails."""
import asyncio
import logging
import re
import urllib.robotparser
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from scraper.filters import normalise_domain, is_blocked, guess_traffic, detect_ads

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

# Link keywords that suggest contact / ad pages
AD_CONTACT_KEYWORDS = [
    "advertise", "advertising", "advertise-with-us", "advertise_with_us",
    "ads", "media-kit", "mediakit", "media_kit",
    "sponsor", "sponsored", "partnership", "partner",
    "contact", "contact-us", "contactus",
    "marketing", "work-with-us",
]

# Email classification by page path
def _classify_email(page_url: str) -> str:
    path = page_url.lower()
    if any(k in path for k in ["advertis", "ads", "sponsor", "media-kit", "mediakit", "partner"]):
        return "advertise"
    if any(k in path for k in ["marketing"]):
        return "marketing"
    if any(k in path for k in ["contact"]):
        return "contact"
    return "general"


async def _check_robots(client: httpx.AsyncClient, base_url: str) -> bool:
    """Return True if scraping is allowed by robots.txt."""
    robots_url = base_url.rstrip("/") + "/robots.txt"
    try:
        resp = await client.get(robots_url, timeout=5)
        if resp.status_code == 200:
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(resp.text.splitlines())
            return rp.can_fetch("*", base_url)
    except Exception:
        pass
    return True   # allow if robots.txt unreachable


async def _fetch(client: httpx.AsyncClient, url: str) -> Optional[Tuple[str, str]]:
    """Fetch URL, return (text, final_url) or None on failure."""
    try:
        resp = await client.get(url, headers=HEADERS, timeout=10, follow_redirects=True)
        if resp.status_code == 200:
            return resp.text, str(resp.url)
    except Exception:
        pass
    return None


def _find_contact_links(html: str, base_url: str) -> List[Tuple[str, str]]:
    """Return list of (url, keyword) for contact/ad sub-pages."""
    soup = BeautifulSoup(html, "html.parser")
    found = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        text = a.get_text(strip=True).lower()
        combined = href + " " + text
        for kw in AD_CONTACT_KEYWORDS:
            if kw in combined:
                full = urljoin(base_url, a["href"])
                # Stay on same domain
                if urlparse(full).netloc == urlparse(base_url).netloc:
                    if full not in seen:
                        seen.add(full)
                        found.append((full, kw))
                break
    return found[:8]   # cap at 8 sub-pages per site


def _extract_emails(html: str) -> List[str]:
    # Also decode mailto: links
    decoded = html.replace("%40", "@").replace("&#64;", "@").replace("&amp;", "&")
    emails = EMAIL_RE.findall(decoded)
    # Filter out obvious false positives
    cleaned = []
    for e in emails:
        e = e.lower().strip(".")
        if any(e.endswith(tld) for tld in [".png", ".jpg", ".gif", ".css", ".js"]):
            continue
        if "example" in e or "domain.com" in e or "yoursite" in e:
            continue
        cleaned.append(e)
    return list(dict.fromkeys(cleaned))   # deduplicate, preserve order


async def parse_site(url: str) -> Optional[dict]:
    """
    Visit a website and return a contact dict or None if nothing useful found.

    Returns:
        {
            website_url: str,   normalised domain
            email: str,         best email found
            email_type: str,
            is_accessible: bool,
            has_ads: bool,
            traffic_guess: str,
            notes: str,
        }
    """
    domain = normalise_domain(url)

    if is_blocked(domain):
        logger.debug("Blocked domain: %s", domain)
        return None

    base_url = f"https://{domain}"

    async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
        # Robots check
        allowed = await _check_robots(client, base_url)
        if not allowed:
            logger.debug("robots.txt disallows: %s", domain)
            return None

        # Fetch homepage
        result = await _fetch(client, base_url)
        if not result:
            # Try http fallback
            result = await _fetch(client, f"http://{domain}")
        if not result:
            return None

        homepage_html, final_url = result
        soup_home = BeautifulSoup(homepage_html, "html.parser")

        has_ads = detect_ads(homepage_html)
        traffic = guess_traffic(homepage_html, soup_home)

        # Collect emails from homepage first
        all_emails: List[Tuple[str, str]] = []  # (email, page_url)
        for e in _extract_emails(homepage_html):
            all_emails.append((e, final_url))

        # Visit contact/advertise sub-pages
        contact_links = _find_contact_links(homepage_html, final_url)
        for sub_url, kw in contact_links:
            await asyncio.sleep(0.5)
            sub_result = await _fetch(client, sub_url)
            if sub_result:
                sub_html, sub_final = sub_result
                for e in _extract_emails(sub_html):
                    all_emails.append((e, sub_final))

    if not all_emails:
        return None

    # Pick the best email: prefer advertise/marketing/contact page emails
    best_email, best_page = all_emails[0]
    for email, page in all_emails:
        etype = _classify_email(page)
        if etype in ("advertise", "marketing"):
            best_email, best_page = email, page
            break

    email_type = _classify_email(best_page)

    # Build notes
    notes_parts = []
    if best_page != final_url:
        path = urlparse(best_page).path
        notes_parts.append(f"Found on {path}")
    if has_ads:
        notes_parts.append("has ad tags")

    return {
        "website_url": domain,
        "email": best_email,
        "email_type": email_type,
        "is_accessible": True,
        "has_ads": has_ads,
        "traffic_guess": traffic,
        "notes": "; ".join(notes_parts),
    }
