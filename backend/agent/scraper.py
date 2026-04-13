"""Scrapes academic job directory pages and stores new Positions in the DB."""
import asyncio
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import httpx
from bs4 import BeautifulSoup
from sqlmodel import Session, select

from core.database import engine
from models.position import Position
from models.source import Source

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Only keep positions whose title/description contains one of these
RELEVANCE_KEYWORDS = {
    "phd", "doctorate", "doctoral", "postdoc", "post-doc",
    "researcher", "fellowship", "graduate student", "research position",
    "research grant", "research fellow",
}


# ── Public entry point ────────────────────────────────────────────────────────

async def scrape_source(source_id: int) -> List[int]:
    """Fetch and parse a source URL; return IDs of newly created Positions."""
    with Session(engine) as session:
        source = session.get(Source, source_id)
        if not source or not source.is_active:
            return []
        url = source.url

    try:
        async with httpx.AsyncClient(
            headers=HEADERS, follow_redirects=True, timeout=30
        ) as client:
            raw = await _fetch_all_pages(client, url)
    except Exception as exc:
        logger.error("Scrape failed for source %s (%s): %s", source_id, url, exc)
        return []

    logger.info("Source %s: scraped %d candidates (before dedup)", source_id, len(raw))

    new_ids: List[int] = []
    with Session(engine) as session:
        for item in raw:
            apply_url = item.get("apply_url", "").strip()
            if not apply_url:
                continue
            if session.exec(
                select(Position).where(Position.apply_url == apply_url)
            ).first():
                continue  # already in DB

            pos = Position(
                source_id=source_id,
                title=item.get("title", "Unknown Title")[:500],
                university=item.get("university", "")[:300],
                country=item.get("country", "")[:100],
                description=item.get("description", ""),
                deadline=item.get("deadline"),
                apply_url=apply_url,
                raw_html=item.get("raw_html", "")[:8000],
            )
            session.add(pos)
            session.commit()
            session.refresh(pos)
            new_ids.append(pos.id)

        src = session.get(Source, source_id)
        if src:
            src.last_scraped_at = datetime.utcnow()
            session.add(src)
            session.commit()

    logger.info("Source %s: %d new positions stored", source_id, len(new_ids))
    return new_ids


# ── Multi-page fetcher ────────────────────────────────────────────────────────

async def _fetch_all_pages(
    client: httpx.AsyncClient, start_url: str, max_pages: int = 5
) -> List[Dict]:
    """Walk paginated results, preserving original query params on every page."""
    results: List[Dict] = []
    current_url: Optional[str] = start_url

    for _ in range(max_pages):
        if not current_url:
            break
        resp = await client.get(current_url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        page_items = await _parse(soup, current_url, client)
        results.extend(page_items)

        # Follow "next page" link while preserving original search params
        next_el = soup.select_one(
            'a[rel="next"], a.next, a.pager__item--next, '
            '.pagination .next a, [aria-label="Next page"]'
        )
        if next_el and next_el.get("href"):
            current_url = _merge_url(start_url, next_el["href"])
        else:
            break

    return results


def _merge_url(original: str, next_href: str) -> str:
    """Merge a pagination href with the original URL, preserving search params."""
    orig = urlparse(original)
    nxt  = urlparse(next_href)

    orig_params = {k: v[0] for k, v in parse_qs(orig.query).items()}
    next_params = {k: v[0] for k, v in parse_qs(nxt.query).items()}
    merged = {**orig_params, **next_params}

    return urlunparse((
        nxt.scheme  or orig.scheme,
        nxt.netloc  or orig.netloc,
        nxt.path    or orig.path,
        "",
        urlencode(merged),
        "",
    ))


# ── Site dispatcher ───────────────────────────────────────────────────────────

async def _parse(
    soup: BeautifulSoup, base_url: str, client: httpx.AsyncClient
) -> List[Dict]:
    if "euraxess.ec.europa.eu" in base_url:
        return await _parse_euraxess(soup, base_url, client)
    if "jobs.ac.uk" in base_url:
        return _parse_jobs_ac_uk(soup, base_url)
    return _parse_generic(soup, base_url)


# ── Euraxess — two-phase: list → detail pages ─────────────────────────────────

async def _parse_euraxess(
    soup: BeautifulSoup, base_url: str, client: httpx.AsyncClient
) -> List[Dict]:
    """Phase 1: collect job URLs from search results.  Phase 2: fetch detail pages."""
    job_urls: List[str] = []
    seen: set = set()

    for a in soup.find_all("a", href=re.compile(r"/jobs/\d+")):
        href = a["href"]
        if not href.startswith("http"):
            href = urljoin("https://euraxess.ec.europa.eu", href)
        if href not in seen:
            seen.add(href)
            job_urls.append(href)

    if not job_urls:
        logger.warning("Euraxess: no job links found on %s", base_url)
        return []

    # Fetch all detail pages concurrently (max 5 at a time)
    sem = asyncio.Semaphore(5)

    async def fetch_detail(url: str):
        async with sem:
            try:
                r = await client.get(url)
                r.raise_for_status()
                return url, r.text
            except Exception as exc:
                logger.warning("Euraxess detail fetch failed %s: %s", url, exc)
                return url, None

    tasks = [fetch_detail(u) for u in job_urls]
    fetched = await asyncio.gather(*tasks)

    results = []
    for url, html in fetched:
        if not html:
            continue
        item = _parse_euraxess_detail(html, url)
        if item and _is_relevant(item):
            results.append(item)

    return results


def _parse_euraxess_detail(html: str, apply_url: str) -> Optional[Dict]:
    """Extract structured data from a single Euraxess job detail page."""
    soup = BeautifulSoup(html, "html.parser")

    # Try specific class first; fall back to OG meta, then any h1
    title_el = soup.select_one("h1.ecl-content-block__title")
    if title_el:
        title = title_el.get_text(strip=True)
    else:
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            title = og["content"].strip()
        else:
            h1 = soup.find("h1")
            if not h1:
                return None
            title = h1.get_text(strip=True)
    if not title or title.lower() in ("job offer", "job"):
        return None

    # Country from highlighted label
    country_el = soup.select_one(".ecl-label--highlight")
    country = country_el.get_text(strip=True) if country_el else ""

    # Structured fields from definition lists
    university = ""
    deadline   = ""
    research_field = ""

    for dl in soup.select("dl.ecl-description-list"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for i, dt in enumerate(dts):
            key = dt.get_text(strip=True)
            val = dds[i].get_text(strip=True) if i < len(dds) else ""
            if ("Organisation" in key or "Company" in key) and not university:
                university = val
            elif "Application Deadline" in key and not deadline:
                deadline = val.split(" - ")[0].strip()  # strip timezone suffix
            elif "Research Field" in key and not research_field:
                research_field = val
            elif key == "Country" and not country:
                country = val

    # Description: find "Offer Description" section heading and grab following text
    description = ""
    for heading in soup.find_all(["h2", "h3"]):
        if "Offer Description" in heading.get_text():
            # Collect text from siblings until next heading
            parts = []
            for sib in heading.find_next_siblings():
                if sib.name in ("h2", "h3"):
                    break
                t = sib.get_text(separator=" ", strip=True)
                if t:
                    parts.append(t)
                if sum(len(p) for p in parts) > 1500:
                    break
            description = " ".join(parts)[:1500]
            break

    if not description and research_field:
        description = research_field

    return {
        "title":       title,
        "university":  university,
        "country":     country,
        "deadline":    deadline or None,
        "description": description,
        "apply_url":   apply_url,
        "raw_html":    "",
    }


# ── jobs.ac.uk ────────────────────────────────────────────────────────────────

def _parse_jobs_ac_uk(soup: BeautifulSoup, base_url: str) -> List[Dict]:
    results = []
    for item in soup.select("div.j-search-result__text, article.j-search-result"):
        # Title link: may be h2/h3 wrapped or a direct first <a>
        title_el = item.select_one("h2 a, h3 a, .j-search-result__h2 a") or item.find("a", href=True)
        if not title_el:
            continue
        href = title_el.get("href", "")
        if not href or href == "#":
            continue
        href = urljoin(base_url, href)
        inst_el = item.select_one(".j-search-result__employer, .employer")
        # Location: dedicated class or plain div containing "Location:"
        loc_el  = item.select_one(".j-search-result__location, .location")
        if not loc_el:
            for div in item.find_all("div"):
                t = div.get_text()
                if "Location:" in t:
                    loc_el = div
                    break
        dl_el   = item.select_one(".j-search-result__close-date, .closing-date")
        country_text = loc_el.get_text(strip=True).replace("Location:", "").strip() if loc_el else ""
        item_data = {
            "title":       title_el.get_text(strip=True),
            "university":  inst_el.get_text(strip=True) if inst_el else "",
            "country":     country_text,
            "deadline":    dl_el.get_text(strip=True)   if dl_el   else None,
            "description": "",
            "apply_url":   href,
            "raw_html":    str(item),
        }
        if _is_relevant(item_data):
            results.append(item_data)
    return results


# ── Generic fallback ──────────────────────────────────────────────────────────

def _parse_generic(soup: BeautifulSoup, base_url: str) -> List[Dict]:
    """Last-resort: collect anchor tags whose text looks like an academic position."""
    results = []
    seen: set = set()

    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        if not href or href in seen or not (10 <= len(text) <= 250):
            continue
        if not _is_relevant({"title": text, "description": ""}):
            continue
        if not href.startswith("http"):
            href = urljoin(base_url, href)
        seen.add(href)

        parent  = a.find_parent(["article", "li", "tr", "div"]) or a.parent
        context = parent.get_text(separator=" ", strip=True)[:600] if parent else ""

        results.append({
            "title":       text,
            "university":  "",
            "country":     "",
            "deadline":    None,
            "description": context,
            "apply_url":   href,
            "raw_html":    str(parent or a),
        })
        if len(results) >= 50:
            break

    return results


# ── Relevance filter ──────────────────────────────────────────────────────────

def _is_relevant(item: Dict) -> bool:
    """Return True if the position looks like a PhD / research opportunity."""
    text = (item.get("title", "") + " " + item.get("description", "")).lower()
    return any(kw in text for kw in RELEVANCE_KEYWORDS)
