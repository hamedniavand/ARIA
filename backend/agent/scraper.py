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

_COUNTRY_HINTS = {
    "United Kingdom": ["uk", "united kingdom", "england", "scotland", "wales", "britain"],
    "Germany":        ["germany", "deutschland", "german"],
    "Netherlands":    ["netherlands", "holland", "dutch"],
    "Belgium":        ["belgium", "belgique", "belgian"],
    "France":         ["france", "french", "paris"],
    "Switzerland":    ["switzerland", "swiss", "zurich", "geneva"],
    "Sweden":         ["sweden", "swedish", "stockholm"],
    "Denmark":        ["denmark", "danish", "copenhagen"],
    "Norway":         ["norway", "norwegian"],
    "Finland":        ["finland", "finnish"],
    "Austria":        ["austria", "austrian", "vienna"],
    "Italy":          ["italy", "italian"],
    "Spain":          ["spain", "spanish"],
    "United States":  ["usa", "united states", "u.s.", "america"],
    "Canada":         ["canada", "canadian"],
    "Australia":      ["australia", "australian"],
    "Japan":          ["japan", "japanese"],
    "China":          ["china", "chinese"],
    "Singapore":      ["singapore"],
    "Hong Kong":      ["hong kong"],
}

# ── Field classifier ─────────────────────────────────────────────────────────

_FIELD_MAP = {
    "Computer Science":      ["computer science", "machine learning", "deep learning", "nlp",
                              "natural language", "artificial intelligence", " ai ", "software",
                              "data science", "cybersecurity", "network", "algorithm", "computing",
                              "information technology", "human-computer"],
    "Biology":               ["biology", "biolog", "biochemi", "microbio", "cell biology",
                              "molecular biology", "genetics", "genomics", "ecology",
                              "evolution", "neuroscience", "bioinformatics", "botany", "zoology"],
    "Chemistry":             ["chemi", "organic chemistry", "inorganic", "materials science",
                              "polymer", "spectroscopy", "catalysis", "synthesis"],
    "Physics":               ["physic", "quantum", "optics", "astrophysics", "condensed matter",
                              "plasma", "particle physics", "photonics", "nuclear"],
    "Engineering":           ["engineering", "mechanical", "electrical", "civil", "aerospace",
                              "robotics", "embedded", "control systems", "biomedical engineering",
                              "chemical engineering", "structural", "manufacturing"],
    "Mathematics":           ["mathemat", "statistics", "probability", "algebra",
                              "topology", "computational math", "numerical analysis", "stochastic"],
    "Medicine & Health":     ["medicine", "medical", "clinical", "pharmacology", "immunology",
                              "epidemiology", "public health", "nursing", "oncology", "pathology",
                              "neurology", "psychiatry", "dentistry", "surgery", "radiology"],
    "Environmental Science": ["environmental", "climate", "sustainability", "energy",
                              "atmospheric", "geoscience", "oceanography", "hydrology",
                              "geologi", "earth science", "remote sensing"],
    "Economics & Business":  ["economics", "business", "finance", "management", "marketing",
                              "accounting", "supply chain", "operations research", "econom"],
    "Social Sciences":       ["psychology", "sociology", "political science", "anthropology",
                              "education", "linguistics", "communication", "social work",
                              "international relations", "criminology", "gender studies"],
    "Humanities":            ["history", "philosophy", "literature", "archaeology",
                              "cultural studies", "languages", "law", "theology",
                              "art history", "musicology", "classics"],
}

def _classify_field(title: str, description: str) -> str:
    """Return the best-matching academic discipline label for a position."""
    text = (title + " " + description).lower()
    best_label, best_hits = "Other", 0
    for label, keywords in _FIELD_MAP.items():
        hits = sum(1 for kw in keywords if kw in text)
        if hits > best_hits:
            best_label, best_hits = label, hits
    return best_label


# ── Deadline regex patterns ───────────────────────────────────────────────────

_DEADLINE_PATTERNS = [
    re.compile(
        r'(?:application\s+deadline|closing\s+date|apply\s+by|deadline\s*:?|closes?[:\s])'
        r'\s*([A-Za-z0-9 ,\-\/\.]+?\d{4})',
        re.I
    ),
    re.compile(
        r'(?:deadline|closing)[:\s]+(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})',
        re.I
    ),
]


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

    raw: List[Dict] = []
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, follow_redirects=True, timeout=30
        ) as client:
            raw = await _fetch_all_pages(client, url)

            # Post-fetch: enrich description + fill in missing deadlines
            for item in raw:
                apply_url = item.get("apply_url", "")
                if apply_url:
                    # Enrich short descriptions by visiting the position page
                    if len(item.get("description", "")) < 400:
                        item = await _enrich_from_url(client, item)
                    # Extract deadline if missing
                    if not item.get("deadline"):
                        dl = await _fetch_deadline_from_url(client, apply_url)
                        if dl:
                            item["deadline"] = dl
                            logger.debug("Deadline fetched for %s: %s", apply_url[:60], dl)
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
                continue  # already in DB (exact URL match)

            if _is_duplicate(session, item.get("title", ""), item.get("university", "")):
                logger.debug("Duplicate skipped: %s @ %s", item.get("title", "")[:60], item.get("university", ""))
                continue

            pos = Position(
                source_id=source_id,
                title=item.get("title", "Unknown Title")[:500],
                university=item.get("university", "")[:300],
                country=item.get("country", "")[:100],
                description=item.get("description", ""),
                deadline=item.get("deadline"),
                field=_classify_field(item.get("title", ""), item.get("description", "")),
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
    if _looks_like_rss(start_url):
        return await _fetch_rss(client, start_url)
    if "t.me" in start_url:
        return await _fetch_telegram(client, start_url)
    if "phdscanner.com" in start_url:
        return await _fetch_phdscanner_api(client, start_url, max_pages=10)
    if "academicpositions.com" in start_url:
        return await _fetch_academicpositions(start_url, max_pages=3)
    # findaphd.com: Cloudflare blocks direct scraping and RSS → use Serper snippets
    if "findaphd.com" in start_url:
        logger.info("findaphd.com → using Serper search (Cloudflare-blocked)")
        return await _fetch_via_serper_snippets(
            queries=[
                "site:findaphd.com/phds/project phd studentship funded",
                "site:findaphd.com/phds/project funded phd fully 2026",
                "site:findaphd.com/phds/program phd studentship 2026",
            ]
        )
    # jobs.ac.uk: RSS returns HTML, direct HTML selectors are fragile → Serper + page fetch
    if "jobs.ac.uk" in start_url:
        logger.info("jobs.ac.uk → using Serper + individual page parse")
        return await _fetch_jobs_ac_uk_via_serper(client)
    if "indeed.com" in start_url:
        fetch_url = start_url
        if not parse_qs(urlparse(start_url).query).get("q"):
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
    if "nature.com" in base_url or "timeshighereducation.com" in base_url:
        return _parse_nature_careers(soup, base_url)
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


# ── RSS / Atom feed support ───────────────────────────────────────────────────

def _looks_like_rss(url: str) -> bool:
    """Return True if the URL looks like an RSS or Atom feed."""
    path     = urlparse(url).path.lower()
    qs       = urlparse(url).query.lower()
    last_seg = path.rstrip("/").rsplit("/", 1)[-1]   # e.g. "jobsrss" from /naturecareers/jobsrss/
    return (
        path.endswith((".rss", ".xml", ".atom"))
        or "/feed" in path
        or "/rss"  in path
        or "rss" in last_seg
        or "feed" in last_seg
        or "feed=rss" in qs
        or "format=rss" in qs
    )


async def _fetch_rss(client: httpx.AsyncClient, url: str) -> List[Dict]:
    """Parse an RSS 2.0 or Atom 1.0 feed and return position dicts."""
    import xml.etree.ElementTree as ET

    try:
        resp = await client.get(url, headers={**HEADERS, "Accept": "application/rss+xml,application/xml,text/xml,*/*"})
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
    except Exception as exc:
        logger.error("RSS fetch/parse failed for %s: %s", url, exc)
        return []

    results: List[Dict] = []
    ns = {"atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/elements/1.1/"}

    # RSS 2.0
    for item in root.findall(".//item"):
        def _t(tag: str) -> str:
            el = item.find(tag)
            return (el.text or "").strip() if el is not None else ""

        title       = _t("title")
        apply_url   = _t("link")
        description = BeautifulSoup(_t("description"), "html.parser").get_text(separator=" ", strip=True)
        university  = _t("dc:creator") or _t("{http://purl.org/dc/elements/1.1/}creator")
        deadline    = _t("pubDate") or None

        pos = {"title": title, "university": university, "country": "",
               "deadline": deadline, "description": description[:1000],
               "apply_url": apply_url, "raw_html": ""}
        if apply_url and _is_relevant(pos):
            results.append(pos)

    # Atom 1.0
    if not results:
        for entry in root.findall("atom:entry", ns) or root.findall("{http://www.w3.org/2005/Atom}entry"):
            def _at(tag: str) -> str:
                el = entry.find(tag, ns) or entry.find("{http://www.w3.org/2005/Atom}" + tag.split(":")[-1])
                return (el.text or "").strip() if el is not None else ""

            title     = _at("atom:title")
            link_el   = entry.find("atom:link", ns) or entry.find("{http://www.w3.org/2005/Atom}link")
            apply_url = (link_el.get("href", "") if link_el is not None else "")
            summary   = BeautifulSoup(_at("atom:summary") or _at("atom:content"), "html.parser").get_text(separator=" ", strip=True)
            author_el = entry.find("atom:author/atom:name", ns)
            university = (author_el.text or "").strip() if author_el is not None else ""

            pos = {"title": title, "university": university, "country": "",
                   "deadline": None, "description": summary[:1000],
                   "apply_url": apply_url, "raw_html": ""}
            if apply_url and _is_relevant(pos):
                results.append(pos)

    logger.info("RSS %s: collected %d items", url, len(results))
    return results


# ── Nature Careers / Times Higher Education (shared HTML structure) ────────────

def _parse_nature_careers(soup: BeautifulSoup, base_url: str) -> List[Dict]:
    """Parse job listings from nature.com/naturecareers or timeshighereducation.com/unijobs."""
    results = []
    for item in soup.select("li.lister__item"):
        title_el = item.select_one("h3.lister__header a, h2.lister__header a")
        if not title_el:
            continue
        href = title_el.get("href", "")
        if not href:
            continue
        if not href.startswith("http"):
            href = urljoin(base_url, href)

        # Metadata list items: typically [institution, location, salary]
        meta_items = [li.get_text(strip=True) for li in item.select("ul.lister__meta li.lister__meta-item")]
        university = meta_items[0] if meta_items else ""
        country    = meta_items[1] if len(meta_items) > 1 else ""

        desc_el = item.select_one("p.lister__description")
        description = desc_el.get_text(separator=" ", strip=True) if desc_el else ""

        # Closing date
        date_el = item.select_one("time, .lister__closing-date, [class*='closing']")
        deadline = date_el.get_text(strip=True) if date_el else None

        pos = {
            "title":       title_el.get_text(strip=True),
            "university":  university,
            "country":     country,
            "deadline":    deadline,
            "description": description,
            "apply_url":   href,
            "raw_html":    "",
        }
        if _is_relevant(pos):
            results.append(pos)
    return results


# ── findaphd.com via playwright-stealth ───────────────────────────────────────

async def _fetch_findaphd(start_url: str, max_pages: int = 5) -> List[Dict]:
    """Scrape findaphd.com using stealth Playwright to bypass Cloudflare JS challenge."""
    from playwright.async_api import async_playwright
    try:
        from playwright_stealth import Stealth
        _stealth = Stealth()
    except ImportError:
        logger.error("playwright-stealth not installed; run: pip install playwright-stealth")
        return []

    parsed = urlparse(start_url)
    qs     = parse_qs(parsed.query)
    keyword = qs.get("Keywords", qs.get("keywords", ["phd"]))[0]

    results: List[Dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="en-GB",
        )
        page = await ctx.new_page()
        await _stealth.apply_stealth_async(page)

        for page_num in range(1, max_pages + 1):
            page_url = f"https://www.findaphd.com/phds/?Keywords={keyword}&PG={page_num}"
            try:
                resp = await page.goto(page_url, wait_until="domcontentloaded", timeout=30_000)
                if resp and resp.status in (403, 429):
                    logger.warning("findaphd.com blocked (page %d): %d", page_num, resp.status)
                    break

                # Check for Cloudflare challenge
                title = await page.title()
                if "just a moment" in title.lower() or "cloudflare" in title.lower():
                    logger.warning("findaphd.com: Cloudflare challenge on page %d", page_num)
                    break

                # Wait for results
                try:
                    await page.wait_for_selector(
                        "div.phd-result, div.ResultItem, .phd-result__title",
                        timeout=10_000
                    )
                except Exception:
                    logger.warning("findaphd.com: no results selector on page %d", page_num)
                    break

                html = await page.content()
            except Exception as exc:
                logger.error("findaphd.com Playwright error page %d: %s", page_num, exc)
                break

            soup = BeautifulSoup(html, "html.parser")
            page_results = _parse_findaphd(soup, start_url)
            if not page_results:
                break
            results.extend(page_results)

        await browser.close()

    logger.info("findaphd.com: collected %d positions", len(results))
    return results


# ── Deadline page fetcher ─────────────────────────────────────────────────────

async def _fetch_deadline_from_url(client: httpx.AsyncClient, apply_url: str) -> Optional[str]:
    """Fetch an individual job page and extract a deadline/closing-date string.
    Tries regex first; falls back to a short Gemini prompt if nothing found."""
    try:
        resp = await client.get(apply_url, timeout=10, follow_redirects=True)
        if resp.status_code != 200:
            return None
        page_text = resp.text[:25000]  # only first 25 KB

        # 1. Fast regex pass
        for pat in _DEADLINE_PATTERNS:
            m = pat.search(page_text)
            if m:
                raw = m.group(1).strip()[:80]
                raw = re.sub(r'\s{2,}', ' ', raw).strip(" .,;")
                if raw:
                    return raw

        # 2. Gemini fallback — only if page has meaningful content
        visible_text = BeautifulSoup(page_text, "html.parser").get_text(separator=" ", strip=True)[:3000]
        if len(visible_text) < 100:
            return None

        from agent.matcher import _gemini
        prompt = (
            "Read the following job posting text and extract ONLY the application deadline or closing date. "
            "Reply with just the date (e.g. '31 January 2026' or '2026-03-15'). "
            "If no deadline is mentioned, reply with the single word: none\n\n"
            f"{visible_text}"
        )
        result = await _gemini(prompt)
        result = result.strip().strip("'\"").strip()
        if result.lower() not in ("none", "n/a", "", "not mentioned", "no deadline"):
            return result[:80]
    except Exception as exc:
        logger.debug("Deadline fetch error %s: %s", apply_url[:60], exc)
    return None


# ── Deep-link enrichment ─────────────────────────────────────────────────────

_AGGREGATOR_DOMAINS = {"academicpositions.com", "findaphd.com", "scholarshipdb.net",
                        "scholarshipportal.com", "euraxess.ec.europa.eu"}


async def _enrich_from_url(client: httpx.AsyncClient, item: Dict) -> Dict:
    """
    Fetch the position's apply_url and extract richer description, supervisor,
    requirements and deadline if not already present.
    For aggregator sites, also try to find the real university URL.
    """
    apply_url = item.get("apply_url", "")
    if not apply_url or not apply_url.startswith("http"):
        return item
    # Don't re-fetch Telegram permalinks or known blocked domains
    skip_domains = {"t.me", "linkedin.com", "twitter.com", "x.com"}
    if any(d in apply_url for d in skip_domains):
        return item

    is_aggregator = any(d in apply_url for d in _AGGREGATOR_DOMAINS)

    try:
        resp = await client.get(apply_url, timeout=12, follow_redirects=True)
        final_url = str(resp.url)

        if resp.status_code not in (200, 301, 302):
            return item

        soup = BeautifulSoup(resp.text[:60000], "html.parser")

        # ── For aggregator pages: find the real external "Apply" link ──────────
        if is_aggregator:
            real_url = _extract_real_apply_url(soup, apply_url)
            if real_url:
                logger.debug("Aggregator %s → real URL: %s", apply_url[:60], real_url[:60])
                item["apply_url"] = real_url
                # Now try to fetch the real page for description
                try:
                    real_resp = await client.get(real_url, timeout=12, follow_redirects=True)
                    if real_resp.status_code == 200:
                        real_soup = BeautifulSoup(real_resp.text[:60000], "html.parser")
                        for tag in real_soup(["script", "style", "nav", "header", "footer", "aside"]):
                            tag.decompose()
                        real_text = real_soup.get_text(separator="\n", strip=True)
                        if len(real_text) > 300:
                            item["description"] = real_text[:4000]
                        return item
                except Exception:
                    pass

        # Remove boilerplate elements
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        visible = soup.get_text(separator="\n", strip=True)
        if len(visible) < 200:
            return item

        # Enrich description if current one is short
        if len(item.get("description", "")) < 300 and len(visible) > 300:
            item["description"] = visible[:4000]

        # Extract university if missing
        if not item.get("university"):
            for line in visible.splitlines():
                if any(k in line for k in ("University", "Institut", "College", "School", "Laboratory", "Center", "Centre")):
                    item["university"] = line.strip()[:150]
                    break

        # Extract country if missing
        if not item.get("country"):
            text_lower = visible.lower()
            for country, hints in _COUNTRY_HINTS.items():
                if any(h in text_lower for h in hints):
                    item["country"] = country
                    break

        logger.debug("Enriched position from %s (%d chars description)", apply_url[:60], len(item.get("description", "")))
    except Exception as exc:
        logger.debug("Enrich failed for %s: %s", apply_url[:60], exc)
    return item


def _extract_real_apply_url(soup: BeautifulSoup, aggregator_url: str) -> Optional[str]:
    """
    Find the real university/lab URL from an aggregator page.
    Looks for 'Apply', 'Visit website', 'More information', external link buttons.
    """
    aggregator_domain = urlparse(aggregator_url).netloc.replace("www.", "")

    # Candidate link texts that likely lead to the real page
    apply_texts = {
        "apply", "apply now", "apply here", "apply online",
        "visit website", "more information", "more info",
        "read more", "view position", "official page",
        "university website", "job posting", "full description",
    }

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        text = a.get_text(strip=True).lower()
        if not href.startswith("http"):
            continue
        link_domain = urlparse(href).netloc.replace("www.", "")
        # Must be external (not the same aggregator)
        if aggregator_domain in link_domain:
            continue
        # Skip known junk
        if any(d in link_domain for d in ("google.com", "facebook.com", "twitter.com",
                                           "linkedin.com", "cloudflare.com", "cookie")):
            continue
        if text in apply_texts or any(kw in text for kw in ("apply", "official", "position", "vacancy")):
            return href

    # Fallback: look for any external link to an .edu / .ac. domain
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href.startswith("http"):
            continue
        link_domain = urlparse(href).netloc
        if aggregator_domain in link_domain:
            continue
        if ".edu" in link_domain or ".ac." in link_domain or "university" in link_domain.lower():
            return href

    return None


# ── Duplicate detection ───────────────────────────────────────────────────────

def _is_duplicate(session, title: str, university: str) -> bool:
    """Return True if a position with the same normalised title+university already exists."""
    if not title or not university:
        return False

    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()

    nt = _norm(title)
    nu = _norm(university)
    if not nt or not nu:
        return False

    for p in session.exec(select(Position)).all():
        if _norm(p.title) == nt and _norm(p.university) == nu:
            return True
    return False


# ── Serper-based scrapers ─────────────────────────────────────────────────────

_SERPER_API_KEY: str = ""   # lazy-loaded from config on first call


def _get_serper_key() -> str:
    global _SERPER_API_KEY
    if not _SERPER_API_KEY:
        from core.config import SERPER_API_KEY
        _SERPER_API_KEY = SERPER_API_KEY
    return _SERPER_API_KEY


async def _serper_search(queries: List[str], n: int = 10) -> List[dict]:
    """Run Serper.dev searches and return deduplicated organic results."""
    key = _get_serper_key()
    if not key:
        logger.warning("SERPER_API_KEY not set — skipping Serper search")
        return []

    seen_urls: set = set()
    results: List[dict] = []

    async with httpx.AsyncClient(timeout=15) as client:
        for query in queries:
            try:
                resp = await client.post(
                    "https://google.serper.dev/search",
                    json={"q": query, "num": n},
                    headers={"X-API-KEY": key, "Content-Type": "application/json"},
                )
                if resp.status_code != 200:
                    logger.warning("Serper %d for query: %s", resp.status_code, query[:60])
                    continue
                data = resp.json()
                # Track usage in shared counter
                try:
                    import sys as _sys, os as _os
                    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "../../"))
                    import serper_counter
                    serper_counter.increment()
                except Exception:
                    pass
                for item in data.get("organic", []):
                    url = item.get("link", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        results.append(item)
            except Exception as exc:
                logger.warning("Serper error: %s", exc)
            await asyncio.sleep(0.3)

    logger.info("Serper: %d unique results from %d queries", len(results), len(queries))
    return results


async def _fetch_via_serper_snippets(queries: List[str]) -> List[Dict]:
    """Use Serper snippets directly as position data (for sites blocking direct fetch).
    Works for findaphd.com where individual pages are 403."""
    organic = await _serper_search(queries, n=10)
    results = []
    for item in organic:
        url   = item.get("link", "")
        title = item.get("title", "").replace(" - FindAPhD.com", "").replace(" | FindAPhD", "").strip()
        snip  = item.get("snippet", "")

        # Only keep individual project/program pages (not listing/search pages)
        if not any(seg in url for seg in ["/project/", "/program/"]):
            continue
        # Skip guide/news/search pages
        if any(seg in url for seg in ["Keywords=", "/guides/", "/news/", "?g0", "?10M"]):
            continue

        # Extract university from title: "PhD in X at University Name" or snippet
        university = ""
        m = re.search(r'\bat\s+((?:[A-Z][a-z]+\s*){1,6})(?:[\|\-,]|$)', title)
        if m:
            university = m.group(1).strip()
        if not university:
            # Try snippet
            ms = re.search(r'\bat\s+((?:[A-Z][a-z]+\s*){1,6})(?:[\|\-,\.])', snip)
            if ms:
                university = ms.group(1).strip()

        pos = {
            "title": title[:500],
            "university": university[:300],
            "country": "",
            "deadline": None,
            "description": snip[:1000],
            "apply_url": url,
            "raw_html": "",
        }
        if _is_relevant(pos):
            results.append(pos)

    logger.info("findaphd Serper snippets: %d positions", len(results))
    return results


async def _fetch_jobs_ac_uk_via_serper(client: httpx.AsyncClient) -> List[Dict]:
    """Discover jobs.ac.uk individual job pages via Serper, then scrape each one."""
    queries = [
        "site:jobs.ac.uk/job phd studentship 2026",
        "site:jobs.ac.uk/job phd studentship funded",
        "site:jobs.ac.uk/job doctoral fellowship 2026",
    ]
    organic = await _serper_search(queries, n=10)

    # Filter to individual job pages only
    job_urls = []
    seen: set = set()
    for item in organic:
        url = item.get("link", "")
        # Individual job pages: /job/JOBID/slug
        if re.search(r"jobs\.ac\.uk/job/[A-Z0-9]+/", url) and url not in seen:
            seen.add(url)
            job_urls.append(url)

    logger.info("jobs.ac.uk: %d individual job URLs from Serper", len(job_urls))
    results = []

    for url in job_urls:
        try:
            resp = await client.get(url, timeout=12, follow_redirects=True)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")

            # Title
            h1 = soup.find("h1")
            title = h1.get_text(strip=True) if h1 else ""
            if not title:
                continue

            # University: from title "... at University" or meta og:title
            university = ""
            m = re.search(r'\bat\s+(.+?)$', title, re.I)
            if m:
                university = m.group(1).strip()
                title = title[:m.start()].strip()
            if not university:
                og = soup.find("meta", property="og:title")
                if og:
                    m2 = re.search(r'\bat\s+(.+?)$', og.get("content", ""), re.I)
                    if m2:
                        university = m2.group(1).strip()

            # Country: jobs.ac.uk is UK-based
            country = "United Kingdom"

            # Deadline: "Closes:" table header → next sibling
            deadline = None
            for th in soup.find_all("th", class_="j-advert-details__table-header"):
                if "closes" in th.get_text(strip=True).lower():
                    td = th.find_next_sibling()
                    if td:
                        deadline = td.get_text(strip=True)
                    break

            # Description: biggest text div (row-8 is common, fall back to og:description)
            desc_el = soup.select_one(".row-8, .j-advert__body, [itemprop=description]")
            if desc_el:
                description = desc_el.get_text(" ", strip=True)[:1500]
            else:
                og_desc = soup.find("meta", attrs={"name": "description"})
                description = og_desc.get("content", "") if og_desc else ""

            pos = {
                "title": title[:500],
                "university": university[:300],
                "country": country,
                "deadline": deadline,
                "description": description,
                "apply_url": url,
                "raw_html": "",
            }
            if _is_relevant(pos):
                results.append(pos)
                logger.debug("jobs.ac.uk: %s @ %s (deadline: %s)", title[:50], university[:40], deadline)

            await asyncio.sleep(0.5)
        except Exception as exc:
            logger.debug("jobs.ac.uk page error %s: %s", url[:60], exc)

    logger.info("jobs.ac.uk Serper+scrape: %d positions", len(results))
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
