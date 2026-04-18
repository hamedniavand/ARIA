# ARIA Changelog

## Phase 10 — Applicant Intelligence & Matching Reliability (2026-04-18)

### Matching System — Rebuilt from scratch
- **Fixed critical bug**: 429 rate-limit errors returned `0.0` score, causing every application to be silently marked `skipped`. Added sentinel value `-1.0` for infrastructure errors; these are never stored and the position is re-evaluated on next scan.
- **New `run_matching_for_applicant()`**: Scores one applicant against all existing positions they haven't been evaluated for yet. New applicants are now automatically matched against all 484+ positions on creation.
- **Field pre-filter**: Eliminates ~50% of Gemini calls by skipping obvious field mismatches (e.g. Humanities/Economics for a CS applicant) without calling the API at all.
- **8× concurrency**: Scoring now runs 8 positions in parallel per applicant instead of sequentially — ~8× faster.
- **Startup resume**: On service restart, any applications stuck in `matched` state (cover letter not yet generated) are automatically picked up and completed.
- **Improved Gemini prompt**: Explicit calibration — does not penalise applicants for missing documents; scores on field + bio when CV is absent; 4-dimension rubric (field alignment 35%, skills match 25%, research fit 25%, profile strength 15%).
- **Smart 429 backoff**: Reads "retry in Xs" from Gemini error body and waits exactly that long before retrying (up to 5 attempts).
- **`.doc` file support**: Word documents now extracted via antiword → raw text fallback (previously silently skipped).
- **Priority score**: `match_score × urgency_multiplier` (2× ≤7 days, 1.75× ≤14, 1.5× ≤30, 1.2× ≤60 days).

### New API Endpoints
- `POST /api/applicants/{id}/match` — trigger on-demand matching for any applicant
- `POST /api/applicants/{id}/viewed` — reset new-match badge
- `GET /api/applicants/{id}/overview` — stats summary (matched, ready, submitted, top 5)
- `GET /api/applicants/{id}/analytics` — funnel + timeline + score distribution
- `GET/POST/PATCH/DELETE /api/applicants/{id}/checklist[/{item_id}]` — per-applicant task checklist
- `POST /api/applicants/{id}/applications/{app_id}/tailored-cv` — Gemini-tailored CV per position
- `PATCH /api/applications/batch` — bulk status update
- `GET /api/serper-usage` — shared Serper quota counter

### Queue view
- Sort by Priority (match × deadline urgency), Match Score, Deadline soonest, Date added
- Deadline urgency badges: 🔥 hot (≤7d), ⚠ warn (≤14d), green (≤30d)
- Match breakdown bars: 4-dimension mini progress bars per application card
- "📄 Tailor CV" button — generates a Gemini-restructured CV for the specific position (600-900 words)

### Applicant cards
- Mini stats row: Matched / Ready / Submitted / Last Match date
- Pulsing "N new" badge when new matches found since last view (resets on card open)
- Expandable detail panel with checklist, documents, and credentials
- **⚡ Match button** — trigger matching for that applicant against all unscored positions

### Per-applicant Analytics (Chart.js)
- Application funnel (Matched → Ready → Submitted)
- Bar chart: applications discovered over time (last 60 days)
- Doughnut chart: match score distribution (90-100 / 75-89 / 55-74 / below 55)

### Scraper improvements
- Aggregator URL resolution: for academicpositions.com and other aggregators, follow "Apply" / "Visit website" external links to store the real university URL instead of the aggregator page
- `_enrich_from_url` visits the real university page for richer description text
- Deadline extraction from position pages (regex patterns for common date formats)

### Infrastructure
- `ChecklistItem` model added to `models/__init__.py` so `init_db()` creates the table on fresh install
- `generate_tailored_cv()` in `generator.py`: Gemini restructures full CV for a specific position
- `generator.py` and `matcher.py` both use the same retry-with-backoff `_gemini()` helper

---

## Phase 9 — Serper Integration & Field/Deadline/RSS Fixes (2026-04-16)

- **findaphd.com** switched to RSS feed (avoids Cloudflare block)
- **jobs.ac.uk** switched to RSS feed (fixes broken selectors)
- **Field classifier** added to `scraper.py` — keyword-based field detection (11 categories)
- **Field column** in Positions table with colour badge
- **Deadline extraction** from position pages via regex patterns
- **Serper usage** tracked in shared `serper_counter.py`; displayed in stats bar
- `/api/serper-usage` endpoint returns `used` / `limit` counts

---

## Phase 8 — Intelligence Upgrades (2026-04)

- Smarter matching — full CV/SOP file text (up to 3 000 chars) sent to Gemini
- Auto-regenerate cover letters when bio/field/language/document changes
- Match yield % per source in Sources table

---

## Phase 7 — More Sources (2026-03)

- nature.com/naturecareers scraper
- timeshighereducation.com/unijobs scraper
- RSS/Atom feed support (auto-detected from URL)
- Cross-source duplicate detection (normalised title+university)

---

## Phase 5 — Automation & Intelligence (2026-02)

- phdscanner.com (JSON API), academicpositions.com (Livewire SPA), ae.indeed.com, Telegram public channels
- Multi-language cover letters (18 languages)
- CAPTCHA solving via CapSolver (reCAPTCHA v2 + hCaptcha)
- Analytics dashboard (funnel, match rate, submission rate)

---

## Phase 4 — Production Server (2026-01)

- HTTP Basic Auth
- systemd service with auto-restart
- Log rotation (daily, 14-day retention)

---

## Phase 3 — Browser Agent (2025-12)

- Playwright Chromium headless agent
- Cookie consent auto-dismiss
- Gemini vision page analysis
- Form filling, CV upload, before/after screenshots
- Sortable tables, applicant filter, batch operations

---

## Phase 2 — Dashboard (2025-11)

- Single-page app (vanilla JS, no framework)
- Sources, Positions, Queue, Errors, Submitted, Applicants views
- Stats bar, live polling

---

## Phase 1 — Core Backend (2025-10)

- FastAPI + SQLModel + SQLite
- Gemini AI via Cloudflare Worker proxy
- EURAXESS + jobs.ac.uk scrapers
- AI match scoring + cover letter generation
- Document upload + AI summarisation
- Portal credentials vault
