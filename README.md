# ARIA — Academic Research & Intelligence Agent

An automated platform that discovers PhD and research positions from multiple job boards and Telegram channels, scores them against applicant profiles using Gemini AI, generates tailored multi-language cover letters, and submits applications via a Playwright browser agent — all from a password-protected web dashboard.

---

## Features

- **Automated scraping** — academicpositions.com (Livewire SPA), EURAXESS, jobs.ac.uk, phdscanner.com (JSON API), ae.indeed.com, Telegram public channels; multi-page pagination support
- **AI matching** — Gemini 2.5 Flash scores each position against each applicant (field alignment, skills, research fit)
- **Multi-language cover letters** — tailored academic cover letters in 18 languages, editable before approval
- **Browser agent** — Playwright navigates to application portals, handles cookie consent, detects and fills forms, takes before/after screenshots
- **CAPTCHA solving** — automatic reCAPTCHA v2 / hCaptcha solving via CapSolver API
- **Multi-applicant** — manage multiple PhD applicants, each with their own documents, credentials, and match queue
- **Document indexing** — upload CV/SOP/references; AI summarises them for better matching
- **Portal credentials vault** — store login credentials per applicant per portal
- **Review queue** — human-in-the-loop: review cover letter, then approve to trigger auto-submission
- **Analytics dashboard** — pipeline funnel, match/submission rates per source and per applicant
- **Rich dashboard** — sortable tables, applicant filter, batch operations, reliability score per source
- **Production-ready** — systemd service, HTTP Basic Auth, log rotation

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
│   │   ├── applicant.py         # Applicant (+ preferred_language), Document
│   │   ├── source.py            # Source (job board URL)
│   │   ├── position.py          # Position (scraped job)
│   │   ├── application.py       # Application + ApplicationStatus enum
│   │   └── portal_credential.py
│   ├── api/
│   │   ├── applicants.py        # CRUD + document upload + credentials
│   │   ├── sources.py           # CRUD + /scan + reliability score
│   │   ├── positions.py         # list/get + batch delete
│   │   └── applications.py      # list/get/patch + approve + retry + batch status
│   └── agent/
│       ├── scraper.py           # Multi-site scraper (EURAXESS, jobs.ac.uk, phdscanner,
│       │                        #   academicpositions, indeed, findaphd, Telegram)
│       ├── matcher.py           # Gemini scoring + cover letter pipeline
│       ├── generator.py         # Gemini cover letter (multi-language) + doc summarisation
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
| findaphd.com | Playwright + stealth | Cloudflare blocks headless — returns 0 |

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
- [ ] findaphd.com — stealth Playwright installed but Cloudflare still blocks headless
- [ ] ScholarshipDB, ResearchGate Jobs
- [ ] University career portals (direct scraping, autonomous method selection)

### 🔲 Phase 8 — Intelligence Upgrades
- [ ] Smarter matching — score against full CV text, not just the AI summary
- [ ] Auto-regenerate cover letters when applicant profile is updated
- [ ] Position quality scoring — rank sources by match yield, not just data completeness

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
| POST | `/api/applicants` | Create applicant |
| PATCH | `/api/applicants/{id}` | Update applicant (incl. preferred_language) |
| POST | `/api/applicants/{id}/documents` | Upload document (CV/SOP/etc.) |
| DELETE | `/api/applicants/{id}/documents/{doc_id}` | Delete document |
| GET | `/api/applicants/{id}/credentials` | List portal credentials |
| POST | `/api/applicants/{id}/credentials` | Save portal credential |
| DELETE | `/api/applicants/{id}/credentials/{cred_id}` | Delete credential |
| GET | `/api/stats` | Pipeline stage counts |
| GET | `/api/analytics` | Match/submission rates by source and applicant |
| GET | `/api/health` | Health check (no auth required) |

---

## License

MIT
