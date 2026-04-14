"""Scrapes academic job directory pages and stores new Positions in the DB."""
import asyncio
import json
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
        raw = []  # fall through so last_scraped_at is still updated below

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
    # Dispatch to specialised fetchers before trying generic HTML
    if "t.me" in start_url:
        return await _fetch_telegram(client, start_url)
    if "phdscanner.com" in start_url:
        return await _fetch_phdscanner_api(client, start_url, max_pages=10)
    if "academicpositions.com" in start_url:
        return await _fetch_academicpositions(start_url, max_pages=3)
    if "findaphd.com" in start_url or "indeed.com" in start_url:
        # For indeed.com bare URLs, append a default PhD search
        fetch_url = start_url
        if "indeed.com" in start_url and not parse_qs(urlparse(start_url).query).get("q"):
            parsed_su = urlparse(start_url)
            fetch_url = urlunparse((
                parsed_su.scheme, parsed_su.netloc, "/jobs",
                "", urlencode({"q": "phd", "l": ""}), ""
            ))
        try:
            html = await _fetch_page_playwright(fetch_url)
        except Exception as exc:
            logger.error("Playwright fetch failed for %s: %s", fetch_url, exc)
            return []
        soup = BeautifulSoup(html, "html.parser")
        return await _parse(soup, fetch_url, client)

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


# ── Telegram public channel scraper ──────────────────────────────────────────

async def _fetch_telegram(client: httpx.AsyncClient, start_url: str, max_pages: int = 5) -> List[Dict]:
    """Scrape a public Telegram channel via t.me/s/{channel}.
    Supports URLs like: https://t.me/phd_positions or https://t.me/s/phd_positions
    """
    parsed = urlparse(start_url)
    # Normalise to /s/ preview URL
    path = parsed.path.rstrip("/")
    if not path.startswith("/s/"):
        path = "/s" + path
    base_preview = f"https://t.me{path}"

    results: List[Dict] = []
    current_url = base_preview

    for _ in range(max_pages):
        try:
            resp = await client.get(current_url, headers={
                **HEADERS,
                "Accept": "text/html,application/xhtml+xml",
            })
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Telegram fetch failed %s: %s", current_url, exc)
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        page_results = _parse_telegram(soup, base_preview)
        if not page_results:
            break
        results.extend(page_results)

        # Pagination: "Load more" link uses ?before=<msg_id>
        load_more = soup.select_one("a.tme_messages_more, a[data-before]")
        if load_more:
            before_id = load_more.get("data-before") or ""
            if before_id:
                current_url = f"{base_preview}?before={before_id}"
            else:
                break
        else:
            break

    logger.info("Telegram %s: collected %d posts", base_preview, len(results))
    return results


def _parse_telegram(soup: BeautifulSoup, channel_url: str) -> List[Dict]:
    """Parse job posts from a Telegram channel preview page."""
    results = []
    seen_urls: set = set()

    for msg in soup.select(".tgme_widget_message"):
        text_el = msg.select_one(".tgme_widget_message_text")
        if not text_el:
            continue
        text = text_el.get_text(separator="\n", strip=True)
        if not text or len(text) < 30:
            continue

        # Permalink to this specific message (used as dedup key and fallback url)
        msg_link = msg.select_one("a.tgme_widget_message_date")
        permalink = msg_link["href"] if msg_link and msg_link.get("href") else ""
        if not permalink or permalink in seen_urls:
            continue
        seen_urls.add(permalink)

        # Prefer a real external apply URL over the telegram permalink
        external_links = [
            a["href"] for a in text_el.find_all("a", href=True)
            if a["href"].startswith("http") and "t.me" not in a["href"]
        ]
        apply_url = external_links[0] if external_links else permalink

        # Collect hashtag values for metadata
        hashtags = [
            a.get_text(strip=True).lstrip("#")
            for a in text_el.select("a[href^='?q=']")
        ]

        # Lines that aren't hashtags and aren't raw URLs
        content_lines = [
            l.strip() for l in text.splitlines()
            if l.strip()
            and not l.strip().startswith("#")
            and not l.strip().startswith("http")
        ]

        # Title: prefer line that mentions a position type; fall back to first long line
        _title_kws = ("phd", "postdoc", "fellowship", "position", "vacancy",
                      "researcher", "studentship", "doctorate", "doctoral")
        title_lines = [l for l in content_lines if len(l) > 10]
        position_lines = [l for l in title_lines if any(k in l.lower() for k in _title_kws)]
        title = (position_lines[0] if position_lines else (title_lines[0] if title_lines else text[:80]))[:150]

        # University: line containing an institution keyword
        university = ""
        for kw in ("University", "Institut", "College", "School", "Laboratory", "Centre", "Center"):
            for line in content_lines:
                if kw in line:
                    university = line[:100]
                    break
            if university:
                break

        # Country: hashtag that isn't a discipline keyword
        _discipline_tags = {
            "phd", "postdoc", "fellowship", "position", "job", "funded",
            "research", "vacancy", "opening", "opportunity", "hiring",
        }
        country_tags = [t for t in hashtags if t.lower() not in _discipline_tags and len(t) <= 25]
        country = country_tags[0].title() if country_tags else ""

        pos = {
            "title":       title,
            "university":  university,
            "country":     country,
            "deadline":    None,
            "description": text[:1000],
            "apply_url":   apply_url,
            "raw_html":    "",
        }
        if _is_relevant(pos):
            results.append(pos)

    return results


# ── phdscanner.com JSON API ───────────────────────────────────────────────────

async def _fetch_phdscanner_api(
    client: httpx.AsyncClient, start_url: str, max_pages: int = 10
) -> List[Dict]:
    """Call the phdscanner.com REST API using offset-based pagination."""
    parsed = urlparse(start_url)
    qs = parse_qs(parsed.query)
    keyword = qs.get("search", qs.get("Keywords", ["phd"]))[0]

    api_base = "https://www.phdscanner.com/api/opportunities"
    limit = 25
    results: List[Dict] = []

    for page in range(max_pages):
        offset = page * limit
        try:
            resp = await client.get(
                api_base,
                params={"limit": limit, "offset": offset, "search": keyword},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("phdscanner API offset=%d failed: %s", offset, exc)
            break

        items = data.get("data", [])
        if not items:
            break

        for item in items:
            city     = item.get("city", "") or ""
            country  = item.get("country", "") or ""
            location = ", ".join(filter(None, [city, country]))
            apply_url = item.get("opportunity_url", "").strip()
            if not apply_url:
                continue
            pos = {
                "title":       item.get("title", "Unknown Title"),
                "university":  item.get("university", ""),
                "country":     location,
                "deadline":    item.get("closing_date") or None,
                "description": item.get("ai_summary", ""),
                "apply_url":   apply_url,
                "raw_html":    "",
            }
            if _is_relevant(pos):
                results.append(pos)

        pagination = data.get("pagination", {})
        total = pagination.get("total", 0)
        if offset + limit >= total:
            break

    logger.info("phdscanner API: collected %d positions", len(results))
    return results


# ── academicpositions.com (Livewire SPA — requires Playwright + networkidle) ──

async def _fetch_academicpositions(start_url: str, max_pages: int = 3) -> List[Dict]:
    """Scrape academicpositions.com using Playwright to execute Livewire rendering."""
    from playwright.async_api import async_playwright
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

    parsed = urlparse(start_url)
    qs = parse_qs(parsed.query)
    # Extract search keyword and positions filter
    search = qs.get("search", ["PHD"])[0]
    positions = qs.get("positions[0]", ["phd"])[0]

    results: List[Dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()

        for page_num in range(1, max_pages + 1):
            page_url = (
                f"https://academicpositions.com/find-jobs"
                f"?page={page_num}&search={search}&positions[0]={positions}"
            )
            try:
                resp = await page.goto(page_url, wait_until="networkidle", timeout=30000)
                if resp and resp.status in (429, 403):
                    logger.warning("academicpositions.com rate-limited (page %d): %d", page_num, resp.status)
                    break
                # Wait for Livewire job cards to appear in the DOM
                try:
                    await page.wait_for_selector("div.job-list-item", timeout=10000)
                except Exception:
                    logger.warning("academicpositions.com: no job cards on page %d", page_num)
                    break

                html = await page.content()
            except Exception as exc:
                logger.error("academicpositions.com Playwright error page %d: %s", page_num, exc)
                break

            soup = BeautifulSoup(html, "html.parser")
            page_results = _parse_academicpositions(soup)
            if not page_results:
                break
            results.extend(page_results)

        await browser.close()

    logger.info("academicpositions.com: collected %d positions", len(results))
    return results


def _parse_academicpositions(soup: BeautifulSoup) -> List[Dict]:
    """Parse Livewire-rendered job cards on academicpositions.com."""
    results = []
    for card in soup.select("div.job-list-item"):
        slug = card.get("data-page-slug", "").strip()
        if not slug:
            continue
        apply_url = f"https://academicpositions.com/academic-jobs/{slug}"

        # Title: prefer h4 inside card, fall back to job-link anchor text
        title_el = card.select_one("h4") or card.select_one("a.hover-title-underline")
        title = title_el.get_text(strip=True) if title_el else slug.replace("-", " ").title()

        # Institution: span or anchor with text-primary class
        inst_el = card.select_one("span.text-primary, a.job-link.text-reset")
        university = inst_el.get_text(strip=True) if inst_el else ""

        # Location: text-muted anchors (city + country)
        loc_els = card.select("a.text-muted")
        country = " ".join(a.get_text(strip=True).rstrip(",") for a in loc_els).strip()

        # Description: first p.text-muted
        desc_el = card.select_one("p.text-muted")
        description = desc_el.get_text(strip=True) if desc_el else ""

        pos = {
            "title":       title,
            "university":  university,
            "country":     country,
            "deadline":    None,
            "description": description,
            "apply_url":   apply_url,
            "raw_html":    "",
        }
        if _is_relevant(pos):
            results.append(pos)
    return results


# ── Playwright-based page fetcher (for Cloudflare-protected sites) ────────────

async def _fetch_page_playwright(url: str, timeout_ms: int = 30_000) -> str:
    """Render a page with Playwright and return the HTML source."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            title = await page.title()
            if "just a moment" in title.lower() or "cloudflare" in title.lower():
                raise RuntimeError(f"Cloudflare challenge on {url}: '{title}'")
            html = await page.content()
        finally:
            await browser.close()

    return html


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
    if "findaphd.com" in base_url:
        return _parse_findaphd(soup, base_url)
    if "indeed.com" in base_url:
        return _parse_indeed(soup, base_url)
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


# ── findaphd.com ─────────────────────────────────────────────────────────────

def _parse_findaphd(soup: BeautifulSoup, base_url: str) -> List[Dict]:
    results = []
    for item in soup.select("div.phd-result, article.result-item, div.ResultItem"):
        title_el = item.select_one("h3 a, h4 a, .phd-result__title a, .ResultTitle a")
        if not title_el:
            continue
        href = title_el.get("href", "")
        if not href:
            continue
        if not href.startswith("http"):
            href = urljoin("https://www.findaphd.com", href)

        inst_el = item.select_one(
            ".phd-result__dept-inst, .result-dept, .ResultInstitution, .phd-result__key-info"
        )
        country_el = item.select_one(".result-country, .phd-result__country")
        deadline_el = item.select_one(".result-deadline, .phd-result__deadline, .ResultDeadline")

        # Try to extract country from institution text if no dedicated element
        inst_text = inst_el.get_text(separator=" ", strip=True) if inst_el else ""
        country_text = country_el.get_text(strip=True) if country_el else ""

        pos = {
            "title":       title_el.get_text(strip=True),
            "university":  inst_text,
            "country":     country_text,
            "deadline":    deadline_el.get_text(strip=True) if deadline_el else None,
            "description": item.get_text(separator=" ", strip=True)[:600],
            "apply_url":   href,
            "raw_html":    str(item),
        }
        if _is_relevant(pos):
            results.append(pos)
    return results


# ── indeed.com ────────────────────────────────────────────────────────────────

def _parse_indeed(soup: BeautifulSoup, base_url: str) -> List[Dict]:
    results = []
    parsed = urlparse(base_url)
    domain_root = f"{parsed.scheme}://{parsed.netloc}"

    for item in soup.select("div.job_seen_beacon, div.result"):
        # Title is in h2; the link anchor carries data-jk
        h2 = item.select_one("h2.jobTitle, h2")
        if not h2:
            continue
        title_text = h2.get_text(strip=True)
        if not title_text:
            continue

        link = item.select_one("a[data-jk]")
        jk = link.get("data-jk", "") if link else ""
        if jk:
            href = f"{domain_root}/viewjob?jk={jk}"
        elif link:
            href = link.get("href", "")
            if href and not href.startswith("http"):
                href = urljoin(domain_root, href)
        else:
            continue

        if not href:
            continue

        company_el  = item.select_one("[data-testid='company-name'], .companyName")
        location_el = item.select_one("[data-testid='text-location'], .companyLocation")

        pos = {
            "title":       title_text,
            "university":  company_el.get_text(strip=True) if company_el else "",
            "country":     location_el.get_text(strip=True) if location_el else "",
            "deadline":    None,
            "description": item.get_text(separator=" ", strip=True)[:600],
            "apply_url":   href,
            "raw_html":    str(item),
        }
        if _is_relevant(pos):
            results.append(pos)
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
