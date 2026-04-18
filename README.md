# ARIA — Academic Research & Intelligence Agent

An automated platform that discovers PhD and research positions from multiple job boards and Telegram channels, scores them against applicant profiles using Gemini AI, generates tailored multi-language cover letters, and submits applications via a Playwright browser agent — all from a password-protected web dashboard.

> **Status:** Phases 1–5, 7–10 complete · Phase 6 (apply reliability) in progress

---

## Features

- **Automated scraping** — 9 sources: EURAXESS, jobs.ac.uk, phdscanner.com, academicpositions.com, ae.indeed.com, nature.com/naturecareers, timeshighereducation.com, Telegram channels, any RSS/Atom feed; multi-page pagination; cross-source duplicate detection
- **AI matching** — Gemini 2.5 Flash scores each position against each applicant using full CV/SOP text; 4-dimension rubric (field alignment, skills, research fit, profile strength); ~8× concurrent scoring; field pre-filter eliminates ~50% of calls without touching the API
- **Priority queue** — combined score = match% × deadline urgency (2× boost for deadlines ≤7 days); urgency badges and 4-bar breakdown visible in queue
- **Multi-language cover letters** — tailored academic cover letters in 18 languages, editable before approval
- **Tailored CV** — Gemini restructures the applicant's full CV specifically for each position (one click in queue)
- **Per-applicant matching** — adding or updating an applicant auto-triggers matching against all existing positions; manual ⚡ Match button available per card
- **Startup resume** — service restart automatically resumes any interrupted cover letter generation
- **Browser agent** — Playwright navigates to application portals, handles cookie consent, detects and fills forms, takes before/after screenshots
- **CAPTCHA solving** — automatic reCAPTCHA v2 / hCaptcha solving via CapSolver API
- **Multi-applicant** — manage multiple PhD applicants, each with their own documents, credentials, match queue, and task checklist
- **Document indexing** — upload CV/SOP/references (PDF, DOCX, DOC, plain text); AI summarises for better matching
- **Portal credentials vault** — store login credentials per applicant per portal
- **Per-applicant analytics** — funnel chart, applications-over-time bar chart, score distribution doughnut (Chart.js)
- **Review queue** — human-in-the-loop: review cover letter, then approve to trigger auto-submission
- **Rich dashboard** — sortable tables, applicant filter, batch operations, reliability score + match yield per source
- **Production-ready** — systemd service, HTTP Basic Auth, log rotation, startup recovery

---

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12 · FastAPI · SQLModel · SQLite |
| AI | Google Gemini 2.5 Flash (via Cloudflare Worker proxy) |
| Browser automation | Playwright (Chromium, headless) |
| CAPTCHA solving | CapSolver API (optional) |
| Frontend | Vanilla JS · Plain HTML/CSS (no framework) |
| Server | Ubuntu VPS · Uvicorn · systemd |

---

## Project Structure

```
ARIA/
├── backend/
│   ├── main.py                  # FastAPI app, static mounts, lifespan, analytics endpoint
│   ├── core/
│   │   ├── config.py            # .env loader, all config vars
│   │   └── database.py          # SQLite engine, init_db()
│   ├── models/
│   │   ├── applicant.py         # Applicant, Document, ChecklistItem
│   │   ├── source.py            # Source (job board URL)
│   │   ├── position.py          # Position (field, deadline, apply_url)
│   │   ├── application.py       # Application + priority_score + match_breakdown + tailored_cv
│   │   └── portal_credential.py
│   ├── api/
│   │   ├── applicants.py        # CRUD + docs + credentials + checklist + overview + analytics + match trigger
│   │   ├── sources.py           # CRUD + /scan + reliability score
│   │   ├── positions.py         # list/get + batch delete
│   │   └── applications.py      # list/get/patch + approve + retry + batch status + sort
│   └── agent/
│       ├── scraper.py           # Multi-site scraper + field classifier + aggregator URL resolver
│       ├── matcher.py           # Gemini scoring (8× concurrent, field pre-filter, priority score)
│       ├── generator.py         # Cover letter + tailored CV + doc summarisation (retry w/ backoff)
│       └── browser.py           # Playwright form detection + CAPTCHA solving + submission
├── frontend/
│   ├── index.html               # SPA shell
│   ├── css/style.css
│   └── js/
│       ├── app.js               # API client, shared state, helpers
│       ├── sources.js           # Sources view (sort, reliability badge, scan)
│       ├── positions.js         # Positions view (applicant filter, sort, batch)
│       ├── applicants.js        # Applicants view (docs, credentials, language)
│       ├── queue.js             # Queue, Errors, Submitted views + screenshots
│       └── analytics.js         # Analytics view (funnel, per-source, per-applicant)
├── /etc/systemd/system/aria.service   # systemd unit (auto-start, auto-restart)
└── /etc/logrotate.d/aria              # Daily log rotation, 14-day retention
```

---

## Setup

### Prerequisites

- Python 3.11+
- A Google Gemini API key (free tier works; enable billing for higher quotas)
- A Cloudflare Worker proxy if your server IP is geo-blocked by Google APIs (see below)
- CapSolver API key (optional, for CAPTCHA solving)

### Install

```bash
git clone https://github.com/hamedniavand/ARIA.git
cd ARIA

python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt

# Install Playwright browser
playwright install chromium
```

### Configure

Create `ARIA/.env`:

```env
GEMINI_API_KEY=your_key_here
GEMINI_PROXY_URL=https://your-cloudflare-worker.workers.dev   # omit if not needed
GEMINI_MODEL=gemini-2.5-flash
SECRET_KEY=change-me-in-production
BASE_URL=http://your-server:8000
DB_PATH=/path/to/ARIA/aria.db
UPLOADS_DIR=/path/to/ARIA/uploads
SCREENSHOTS_DIR=/path/to/ARIA/screenshots
DASHBOARD_USER=admin
DASHBOARD_PASS=your-strong-password
CAPTCHA_API_KEY=your-capsolver-key   # optional
FINDAPHD_PROXY=                      # optional — residential proxy for findaphd.com, e.g. http://user:pass@brd.superproxy.io:22225
```

### Run (development)

```bash
cd ARIA/backend
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` in your browser and enter your dashboard credentials.

### Run (production — systemd)

```bash
# Install service
sudo cp /path/to/aria.service /etc/systemd/system/aria.service
sudo mkdir -p /var/log/aria
sudo systemctl daemon-reload
sudo systemctl enable aria
sudo systemctl start aria

# Useful commands
sudo systemctl status aria        # check status
sudo systemctl restart aria       # restart after config changes
sudo journalctl -u aria -f        # live logs
```

### Gemini Proxy (optional)

If your server IP is blocked by Google APIs, deploy a Cloudflare Worker:

```js
export default {
  async fetch(request) {
    const url = new URL(request.url);
    url.hostname = "generativelanguage.googleapis.com";
    return fetch(new Request(url.toString(), request));
  }
};
```

Set `GEMINI_PROXY_URL` to your Worker URL.

### findaphd.com — Residential Proxy (optional)

findaphd.com is behind Cloudflare's **managed challenge**, which blocks all datacenter IPs regardless of browser fingerprinting. The only reliable bypass is routing traffic through a **residential IP**. Two recommended providers:

#### Option A — Bright Data

1. Sign up at [brightdata.com](https://brightdata.com) → create a **Residential** proxy zone
2. Get your proxy endpoint: `http://USERNAME:PASSWORD@brd.superproxy.io:22225`
3. Add to `.env`:

```env
FINDAPHD_PROXY=http://USERNAME:PASSWORD@brd.superproxy.io:22225
```

Pricing: pay-as-you-go from ~$8.40/GB. Free trial available.

#### Option B — Oxylabs

1. Sign up at [oxylabs.io](https://oxylabs.io) → create a **Residential Proxies** subscription
2. Get your endpoint: `http://USERNAME:PASSWORD@pr.oxylabs.io:7777`
3. Add to `.env`:

```env
FINDAPHD_PROXY=http://USERNAME:PASSWORD@pr.oxylabs.io:7777
```

Pricing: from $8/GB. 7-day free trial available.

#### Wiring the proxy into ARIA

Once `FINDAPHD_PROXY` is set in `.env`, add it to `core/config.py`:

```python
FINDAPHD_PROXY = os.getenv("FINDAPHD_PROXY", "")
```

Then in `backend/agent/scraper.py`, pass it to the Playwright context inside `_fetch_findaphd()`:

```python
from core.config import FINDAPHD_PROXY
# ...
ctx = await browser.new_context(
    proxy={"server": FINDAPHD_PROXY} if FINDAPHD_PROXY else None,
    ...
)
```

> Note: Both `camoufox` and `rebrowser-playwright` were tested — both return 403 without a residential proxy. The block is IP-based, not fingerprint-based.

---

## Usage

1. **Add sources** — Sources → Add Source, paste a job board URL or Telegram channel link (e.g. `https://t.me/phd_positions`)
2. **Add applicants** — Applicants → Add Applicant, fill in bio and field of study, select cover letter language; upload CV/SOP
3. **Scan** — click Scan on a source; ARIA scrapes positions, scores them against all applicants, and generates cover letters for matches
4. **Review queue** — Queue view; read and edit the cover letter, then click Approve & Submit
5. **Browser agent** — ARIA opens the job portal headlessly, fills the form, solves CAPTCHAs if needed, takes screenshots
6. **Track** — Submitted view shows all submitted applications with submission date and screenshots
7. **Analytics** — Analytics view shows match rates, submission rates, and performance breakdown per source and applicant

---

## Supported Sources

| Site | Method | Notes |
|------|--------|-------|
| academicpositions.com | Playwright + Livewire wait | ~10 results/page |
| euraxess.ec.europa.eu | HTTP + two-phase scraping | Detail-page parsing |
| jobs.ac.uk | HTTP + BeautifulSoup | |
| phdscanner.com | JSON API (offset pagination) | ~250 results/scan |
| ae.indeed.com | Playwright | ~10-16 results/scan |
| t.me/{channel} | HTTP (public preview) | Any public Telegram channel |
| nature.com/naturecareers | HTTP + BeautifulSoup | ~20 results/page |
| timeshighereducation.com/unijobs | HTTP + BeautifulSoup | ~30 results/page |
| Any RSS/Atom feed | HTTP + XML parsing | Auto-detected from URL |
| findaphd.com | Playwright + stealth | Cloudflare managed-challenge blocks all headless browsers (datacenter IP); requires residential proxy — see below |

---

## Roadmap

### ✅ Phase 1 — Core Backend
- [x] FastAPI + SQLModel + SQLite (Source, Position, Application, Applicant, Document, PortalCredential)
- [x] Full REST API (CRUD for all resources)
- [x] Gemini AI integration via Cloudflare Worker proxy (bypasses geo-restrictions)
- [x] Scraper: EURAXESS (two-phase list → detail pages), jobs.ac.uk, generic fallback
- [x] AI match scoring with reasoning (0–100, threshold configurable)
- [x] Cover letter generation (400–600 words, tailored per position)
- [x] Document upload + AI summarisation (CV, SOP, references, portfolio)
- [x] Portal credentials vault per applicant

### ✅ Phase 2 — Dashboard
- [x] Single-page app (vanilla JS, hash routing, no framework)
- [x] Sources view — add/pause/delete, scan button with live polling
- [x] Positions view — all positions with match scores, status badges, search
- [x] Queue view — cover letter editor with auto-save, approve/skip
- [x] Errors view — retry failed applications
- [x] Submitted view — submission log
- [x] Applicants view — manage applicants, upload docs, store credentials
- [x] Stats bar — live pipeline stage counts

### ✅ Phase 3 — Browser Agent + UX Polish
- [x] Playwright browser agent (Chromium headless)
- [x] Cookie consent auto-dismiss
- [x] Gemini vision page analysis (form fields, login pages, CAPTCHA detection)
- [x] Automatic form filling (name, email, cover letter, CV upload)
- [x] Before/after screenshots served via `/screenshots/`
- [x] Sortable table columns (click header to sort ▲▼)
- [x] Applicant filter on Positions page (per-applicant score/status view)
- [x] Batch operations — delete positions, bulk status change
- [x] Scrape reliability score per source (data completeness %)

### ✅ Phase 4 — Production Server
- [x] HTTP Basic Auth on dashboard (password protection via `.env`)
- [x] systemd service (`aria.service`) for auto-restart on reboot
- [x] Log rotation (`/etc/logrotate.d/aria`) — daily, 14-day retention

### ✅ Phase 5 — Automation & Intelligence
- [x] More scrapers: phdscanner.com (JSON API), academicpositions.com (Livewire SPA), ae.indeed.com, Telegram public channels
- [x] Multi-language cover letters (18 languages, per-applicant setting)
- [x] CAPTCHA solving (CapSolver integration — reCAPTCHA v2 + hCaptcha)
- [x] Analytics dashboard (pipeline funnel, match rate, submission rate by source and applicant)

### 🔲 Phase 6 — Apply Fix
- [ ] Reliable form submission across common portal types (jobs.ac.uk, EURAXESS, university HR systems)
- [ ] Smarter multi-step navigation: listing → apply page → form, with re-analysis at each step
- [ ] Better field mapping: robust detection of name / email / cover letter / CV upload fields
- [ ] Login flow handling: detect and complete multi-step login before reaching the form
- [ ] Graceful fallback: if form cannot be auto-filled, set status to "manual" with a direct link and screenshots
- *Correctness over speed — Playwright browser approach preferred even if slower*

### ✅ Phase 7 — More Sources
- [x] nature.com/naturecareers — HTTP scraper (20+ results/page)
- [x] timeshighereducation.com/unijobs — HTTP scraper (shared parser with Nature Careers)
- [x] RSS/Atom feed support — any source exposing a feed, auto-detected from URL
- [x] Duplicate detection — normalised title+university comparison across all sources
- [ ] findaphd.com — `playwright-stealth` + `camoufox` both tested, both blocked at IP level; requires residential proxy (Bright Data / Oxylabs) — see setup guide above
- [ ] ScholarshipDB, ResearchGate Jobs
- [ ] University career portals (direct scraping, autonomous method selection)

### ✅ Phase 8 — Intelligence Upgrades
- [x] Smarter matching — scoring now uses full CV/SOP file text (up to 3 000 chars) alongside the AI summary, giving Gemini richer signal on skills, publications, and experience
- [x] Auto-regenerate cover letters — updating bio / field of study / language, or uploading a new document, automatically re-generates all `ready` cover letters for that applicant
- [x] Match yield per source — Sources table now shows "Match Yield %": fraction of positions from each source that resulted in ≥1 match, sortable column

### ✅ Phase 10 — Applicant Intelligence & Matching Reliability
- [x] **Fixed critical matching bug** — 429 errors silently marked all apps as skipped; now uses sentinel value and retries with API-provided wait time
- [x] **Per-applicant matching** — new applicants auto-matched against all existing positions; manual ⚡ Match button per card
- [x] **8× concurrent scoring** — parallel Gemini calls per batch (was sequential)
- [x] **Field pre-filter** — eliminates ~50% of Gemini calls for obvious field mismatches
- [x] **Startup resume** — service restart resumes interrupted cover letter generation
- [x] **Priority score** — match% × deadline urgency multiplier; urgency badges in queue (🔥 ≤7d, ⚠ ≤14d)
- [x] **Sort controls** — queue sortable by priority / score / deadline / date
- [x] **Match breakdown** — 4-bar breakdown per queue card (field, skills, research, profile)
- [x] **Tailored CV** — "📄 Tailor CV" button generates position-specific CV restructured by Gemini
- [x] **Per-applicant analytics** — funnel, timeline bar chart, score distribution doughnut (Chart.js)
- [x] **Applicant overview** — mini stats row + expandable detail panel per card
- [x] **Task checklist** — per-applicant checklist with checkbox state, add/delete
- [x] **New-match badge** — pulsing badge shows count since last view; resets on card open
- [x] **Last matched timestamp** on applicant card
- [x] **PDF + DOCX + DOC** document support for CV extraction
- [x] **Aggregator URL resolution** — academicpositions.com and similar now store real university URL
- [x] **Improved Gemini prompt** — explicit calibration; scores on bio+field when CV absent

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/sources` | List sources with position count + reliability score |
| POST | `/api/sources` | Add source |
| PATCH | `/api/sources/{id}` | Update source (label, url, is_active) |
| DELETE | `/api/sources/{id}` | Delete source |
| POST | `/api/sources/{id}/scan` | Trigger scrape + match pipeline |
| GET | `/api/positions` | List positions (filter: source_id, search) |
| DELETE | `/api/positions/batch` | Delete positions by ID list |
| GET | `/api/applications` | List applications (filter: status, applicant_id, position_id) |
| PATCH | `/api/applications/batch` | Bulk status update |
| PATCH | `/api/applications/{id}` | Update cover letter or status |
| POST | `/api/applications/{id}/approve` | Approve → trigger browser submission |
| POST | `/api/applications/{id}/retry` | Retry a failed application |
| GET | `/api/applications/{id}/screenshots` | List submission screenshots |
| GET | `/api/applicants` | List applicants |
| POST | `/api/applicants` | Create applicant (auto-triggers matching) |
| PATCH | `/api/applicants/{id}` | Update applicant |
| DELETE | `/api/applicants/{id}` | Delete applicant |
| POST | `/api/applicants/{id}/match` | Trigger matching against all unscored positions |
| POST | `/api/applicants/{id}/viewed` | Reset new-match badge |
| GET | `/api/applicants/{id}/overview` | Stats summary + top 5 matches |
| GET | `/api/applicants/{id}/analytics` | Funnel + timeline + score distribution |
| POST | `/api/applicants/{id}/documents` | Upload document (CV/SOP/etc.) |
| DELETE | `/api/applicants/{id}/documents/{doc_id}` | Delete document |
| GET | `/api/applicants/{id}/credentials` | List portal credentials |
| POST | `/api/applicants/{id}/credentials` | Save portal credential |
| DELETE | `/api/applicants/{id}/credentials/{cred_id}` | Delete credential |
| GET | `/api/applicants/{id}/checklist` | List checklist items |
| POST | `/api/applicants/{id}/checklist` | Add checklist item |
| PATCH | `/api/applicants/{id}/checklist/{item_id}` | Update checklist item |
| DELETE | `/api/applicants/{id}/checklist/{item_id}` | Delete checklist item |
| POST | `/api/applicants/{id}/applications/{app_id}/tailored-cv` | Generate Gemini-tailored CV |
| GET | `/api/stats` | Pipeline stage counts |
| GET | `/api/analytics` | Match/submission rates by source and applicant |
| GET | `/api/serper-usage` | Shared Serper quota counter |
| GET | `/api/health` | Health check (no auth required) |

---

## License

MIT
