"""Playwright browser agent — navigate, detect form, fill, screenshot, submit."""
import base64
import json
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.async_api import async_playwright

from core.config import (
    GEMINI_API_KEY, GEMINI_PROXY_URL, GEMINI_MODEL,
    SCREENSHOTS_DIR, UPLOADS_DIR, CAPTCHA_API_KEY,
)

logger = logging.getLogger(__name__)

_SHOTS = Path(SCREENSHOTS_DIR)
_UPLOADS = Path(UPLOADS_DIR)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
}


# ── Public entry point ────────────────────────────────────────────────────────

async def submit_application(application_id: int) -> None:
    """Navigate to the job portal, fill the form, submit, update Application row."""
    from sqlmodel import Session, select
    from core.database import engine
    from models.application import Application, ApplicationStatus
    from models.position import Position
    from models.applicant import Applicant, Document
    from models.portal_credential import PortalCredential

    with Session(engine) as session:
        app = session.get(Application, application_id)
        if not app:
            return
        pos  = session.get(Position, app.position_id)
        appl = session.get(Applicant, app.applicant_id)
        docs = session.exec(
            select(Document).where(Document.applicant_id == appl.id)
        ).all()

        domain = urlparse(pos.apply_url).netloc
        domain_root = ".".join(domain.split(".")[-2:])  # e.g. jobs.ac.uk
        cred = session.exec(
            select(PortalCredential)
            .where(PortalCredential.applicant_id == appl.id)
            .where(PortalCredential.portal_domain.contains(domain_root))
        ).first()

        # Snapshot data (session closes after this block)
        pos_snap  = {"title": pos.title, "apply_url": pos.apply_url,
                     "university": pos.university, "description": pos.description or ""}
        appl_snap = {"name": appl.name, "email": appl.email,
                     "bio": appl.bio or "", "field": appl.field_of_study or ""}
        cover_letter = app.cover_letter or ""
        cv_paths = [
            _UPLOADS / str(d.applicant_id) / d.filename
            for d in docs if d.doc_type == "cv"
        ]
        cred_snap = {"username": cred.username, "password": cred.password} if cred else None

    try:
        notes = await _run_browser(
            application_id, pos_snap, appl_snap,
            cover_letter, cv_paths, cred_snap
        )
        final_status = ApplicationStatus.submitted
        submitted_at = datetime.utcnow()
        err_msg = notes or ""
    except Exception as exc:
        logger.error("Browser agent app %s failed: %s", application_id, exc)
        final_status = ApplicationStatus.error
        submitted_at = None
        err_msg = str(exc)[:500]

    with Session(engine) as session:
        app = session.get(Application, application_id)
        if app:
            app.status = final_status
            if submitted_at:
                app.submitted_at = submitted_at
            app.error_message = err_msg
            session.add(app)
            session.commit()


# ── Browser workflow ──────────────────────────────────────────────────────────

async def _run_browser(
    app_id: int, pos: dict, appl: dict,
    cover_letter: str, cv_paths: list, cred: dict | None
) -> str:
    """Return a short status note string on success; raise on failure."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            extra_http_headers=BROWSER_HEADERS,
        )
        page = await context.new_page()

        try:
            # 1. Navigate to application page
            await page.goto(pos["apply_url"], wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2000)

            # 1a. Auto-dismiss cookie consent banners
            await _dismiss_cookie_consent(page)

            # 2. Before-screenshot
            before_path = _SHOTS / f"app_{app_id}_before.png"
            await page.screenshot(path=str(before_path), full_page=False)
            logger.info("App %s: screenshot saved %s", app_id, before_path.name)

            # 3. Analyse page and determine action
            analysis = await _analyse_page(page)
            logger.info("App %s: page analysis: %s", app_id, analysis.get("type"))

            page_type = analysis.get("type", "unknown")

            # 4. Handle login if required
            if page_type == "login" and cred:
                await _do_login(page, analysis, cred)
                await page.wait_for_timeout(2000)
                analysis = await _analyse_page(page)
                page_type = analysis.get("type", "unknown")

            elif page_type == "login" and not cred:
                raise RuntimeError(
                    "Portal login required but no credentials saved. "
                    "Add credentials in the Applicants tab."
                )

            # 5. Fill application form
            if page_type == "form":
                await _fill_form(page, analysis, appl, cover_letter, cv_paths)

                # Pre-submit screenshot
                prefill_path = _SHOTS / f"app_{app_id}_prefill.png"
                await page.screenshot(path=str(prefill_path), full_page=False)

                # Submit
                await _submit_form(page, analysis)
                await page.wait_for_timeout(3000)

                # Post-submit screenshot
                submitted_path = _SHOTS / f"app_{app_id}_submitted.png"
                await page.screenshot(path=str(submitted_path), full_page=False)

                return "Form submitted via Playwright"

            elif page_type == "captcha":
                solved = await _solve_captcha(page, analysis)
                if solved:
                    await page.wait_for_timeout(2000)
                    analysis = await _analyse_page(page)
                    page_type = analysis.get("type", "unknown")
                    if page_type == "form":
                        await _fill_form(page, analysis, appl, cover_letter, cv_paths)
                        prefill_path = _SHOTS / f"app_{app_id}_prefill.png"
                        await page.screenshot(path=str(prefill_path), full_page=False)
                        await _submit_form(page, analysis)
                        await page.wait_for_timeout(3000)
                        submitted_path = _SHOTS / f"app_{app_id}_submitted.png"
                        await page.screenshot(path=str(submitted_path), full_page=False)
                        return "Form submitted after CAPTCHA solve"
                raise RuntimeError("CAPTCHA detected — could not solve automatically")

            elif page_type == "info":
                # Listing/info page — try to find and click an Apply button
                clicked = await _click_apply_link(page)
                if clicked:
                    await page.wait_for_timeout(3000)
                    await _dismiss_cookie_consent(page)
                    analysis = await _analyse_page(page)
                    page_type = analysis.get("type", "unknown")
                    logger.info("App %s: after Apply click, new type=%s", app_id, page_type)

                if page_type != "form":
                    instructions = analysis.get("instructions", "")
                    raise RuntimeError(
                        f"No application form found on page. "
                        f"Instructions: {instructions[:200]}"
                    )

            else:
                raise RuntimeError(
                    f"Unrecognised page type '{page_type}'. Manual review needed."
                )

        finally:
            await browser.close()


# ── Apply link handler ───────────────────────────────────────────────────────

async def _click_apply_link(page) -> bool:
    """Try to find and click an 'Apply' button/link on a job listing page."""
    apply_selectors = [
        'a:has-text("Apply Now")',
        'a:has-text("Apply now")',
        'a:has-text("Apply")',
        'button:has-text("Apply Now")',
        'button:has-text("Apply now")',
        'button:has-text("Apply")',
        'a[href*="apply"]',
        'a[href*="application"]',
        '[class*="apply-btn"]',
        '[class*="applyBtn"]',
        '[id*="apply"]',
    ]
    for sel in apply_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                href = await loc.get_attribute("href")
                if href and ("mailto:" in href or "tel:" in href):
                    continue  # skip email/tel links
                await loc.click(timeout=5000)
                await page.wait_for_load_state("domcontentloaded", timeout=15_000)
                logger.info("Clicked Apply link: %s", sel)
                return True
        except Exception:
            continue
    return False


# ── Cookie consent handler ───────────────────────────────────────────────────

async def _dismiss_cookie_consent(page) -> None:
    """Click 'Accept' / 'Accept all' / 'I agree' on common cookie consent dialogs."""
    accept_patterns = [
        'button:has-text("Accept all")',
        'button:has-text("Accept All")',
        'button:has-text("Accept cookies")',
        'button:has-text("Accept Cookies")',
        'button:has-text("I accept")',
        'button:has-text("I Accept")',
        'button:has-text("I agree")',
        'button:has-text("Agree")',
        'button:has-text("OK")',
        'button:has-text("Got it")',
        'a:has-text("Accept all")',
        'a:has-text("Accept All")',
        '[id*="accept"]:not([id*="decline"])',
        '[class*="accept-all"]',
        '[class*="acceptAll"]',
        '#onetrust-accept-btn-handler',
        '.cc-accept',
        '.cookie-accept',
    ]
    for sel in accept_patterns:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.click(timeout=3000)
                await page.wait_for_timeout(1000)
                logger.info("Dismissed cookie consent via: %s", sel)
                return
        except Exception:
            continue


# ── Gemini vision helpers ─────────────────────────────────────────────────────

async def _analyse_page(page) -> dict:
    """Screenshot the page and ask Gemini what type it is and what fields it has."""
    png_bytes = await page.screenshot(full_page=False)
    b64 = base64.b64encode(png_bytes).decode()

    prompt = """Analyse this screenshot of an academic job application page.
Return ONLY valid JSON, no markdown.

Determine:
- "type": one of "form" | "login" | "captcha" | "info" | "unknown"
- "fields": list of visible form fields, each with {"label": str, "type": "text|email|textarea|file|select|checkbox", "selector_hint": str}
- "submit_text": visible text on the submit/apply button (empty string if not found)
- "login_fields": present only for login pages — {"username_label": str, "password_label": str}
- "instructions": for "info" pages — brief text of what the page says to do

Example for a form page:
{"type":"form","fields":[{"label":"Full Name","type":"text","selector_hint":"input[name*=name]"},{"label":"Cover Letter","type":"textarea","selector_hint":"textarea"}],"submit_text":"Apply Now","login_fields":{}}
"""

    data = await _gemini_vision(prompt, b64)
    try:
        text = data.lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(text)
    except Exception:
        logger.warning("Failed to parse page analysis JSON: %s", data[:200])
        return {"type": "unknown"}


async def _gemini_vision(prompt: str, image_b64: str) -> str:
    """Call Gemini with text + image via proxy."""
    url = f"{GEMINI_PROXY_URL}/v1beta/models/{GEMINI_MODEL}:generateContent"
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/png", "data": image_b64}},
            ]
        }]
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            url, json=payload, headers={"X-goog-api-key": GEMINI_API_KEY}
        )
        resp.raise_for_status()
        result = resp.json()
        return result["candidates"][0]["content"]["parts"][0]["text"].strip()


# ── Form interaction ──────────────────────────────────────────────────────────

async def _do_login(page, analysis: dict, cred: dict) -> None:
    """Attempt to log in using portal credentials."""
    lf = analysis.get("login_fields", {})
    username_hint = lf.get("username_label", "email")
    password_hint = lf.get("password_label", "password")

    # Try to fill username
    for sel in [
        f'input[type="email"]',
        f'input[type="text"][name*="user"]',
        f'input[name*="email"]',
        f'input[placeholder*="{username_hint}"]',
        'input[type="text"]:first-of-type',
    ]:
        try:
            if await page.locator(sel).count() > 0:
                await page.fill(sel, cred["username"])
                break
        except Exception:
            continue

    # Try to fill password
    try:
        await page.fill('input[type="password"]', cred["password"])
    except Exception:
        pass

    # Submit login form
    for sel in ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("Login")', 'button:has-text("Sign in")']:
        try:
            if await page.locator(sel).count() > 0:
                await page.click(sel)
                break
        except Exception:
            continue

    await page.wait_for_load_state("domcontentloaded")
    logger.info("Login attempt completed")


async def _fill_form(page, analysis: dict, appl: dict, cover_letter: str, cv_paths: list) -> None:
    """Fill visible form fields based on Gemini's field analysis."""
    fields = analysis.get("fields", [])

    for field in fields:
        label = field.get("label", "").lower()
        ftype = field.get("type", "text")
        hint  = field.get("selector_hint", "")

        value = _map_field_to_value(label, ftype, appl, cover_letter)
        if not value and ftype != "file":
            continue

        # Build a list of selectors to try
        selectors = []
        if hint:
            selectors.append(hint)
        selectors += _label_selectors(label)

        if ftype == "file" and cv_paths:
            cv_path = str(cv_paths[0]) if cv_paths else None
            if cv_path:
                for sel in selectors + ['input[type="file"]']:
                    try:
                        if await page.locator(sel).count() > 0:
                            await page.set_input_files(sel, cv_path)
                            logger.info("Uploaded CV to %s", sel)
                            break
                    except Exception:
                        continue
        elif ftype == "textarea":
            for sel in selectors + ["textarea"]:
                try:
                    if await page.locator(sel).count() > 0:
                        await page.fill(sel, value)
                        break
                except Exception:
                    continue
        else:
            for sel in selectors:
                try:
                    if await page.locator(sel).count() > 0:
                        await page.fill(sel, value)
                        break
                except Exception:
                    continue


def _map_field_to_value(label: str, ftype: str, appl: dict, cover_letter: str) -> str:
    """Return the value to use for a field based on its label."""
    if ftype == "file":
        return "__file__"
    if any(k in label for k in ["cover letter", "letter", "motivation", "personal statement"]):
        return cover_letter
    if any(k in label for k in ["first name", "given name", "forename"]):
        return appl["name"].split()[0] if " " in appl["name"] else appl["name"]
    if any(k in label for k in ["last name", "surname", "family name"]):
        return appl["name"].split()[-1] if " " in appl["name"] else ""
    if any(k in label for k in ["full name", "name"]):
        return appl["name"]
    if any(k in label for k in ["email", "e-mail"]):
        return appl["email"]
    if any(k in label for k in ["research interest", "research area", "field of study"]):
        return appl["field"]
    if any(k in label for k in ["statement", "background", "description", "bio"]):
        return appl["bio"][:1000]
    return ""


def _label_selectors(label: str) -> list:
    """Generate CSS/Playwright selectors from a field label."""
    slug = label.replace(" ", "")
    return [
        f'input[name*="{slug}"]',
        f'input[placeholder*="{label}"]',
        f'textarea[name*="{slug}"]',
        f'textarea[placeholder*="{label}"]',
        f'label:has-text("{label}") + input',
        f'label:has-text("{label}") + textarea',
    ]


async def _solve_captcha(page, analysis: dict) -> bool:
    """Attempt to solve a CAPTCHA using CapSolver API. Returns True if solved."""
    from core.config import CAPTCHA_API_KEY
    if not CAPTCHA_API_KEY:
        logger.warning("CAPTCHA detected but CAPTCHA_API_KEY not set")
        return False

    url = page.url
    captcha_type = analysis.get("captcha_type", "").lower()

    try:
        # Detect sitekey from page HTML
        html = await page.content()
        import re

        # reCAPTCHA v2
        recaptcha_match = re.search(
            r'["\']?(?:data-sitekey|sitekey)["\']?\s*[=:]\s*["\']([^"\']{20,})["\']',
            html, re.IGNORECASE
        )
        # hCaptcha
        hcaptcha_match = re.search(
            r'hcaptcha\.com.*?data-sitekey=["\']([^"\']+)["\']', html, re.IGNORECASE
        )

        async with httpx.AsyncClient(timeout=120) as client:
            if hcaptcha_match:
                sitekey = hcaptcha_match.group(1)
                logger.info("Attempting hCaptcha solve, sitekey=%s", sitekey[:10])
                task = {"type": "HCaptchaTaskProxyLess", "websiteURL": url, "websiteKey": sitekey}
            elif recaptcha_match:
                sitekey = recaptcha_match.group(1)
                logger.info("Attempting reCAPTCHA solve, sitekey=%s", sitekey[:10])
                task = {"type": "ReCaptchaV2TaskProxyLess", "websiteURL": url, "websiteKey": sitekey}
            else:
                logger.warning("CAPTCHA sitekey not found in page HTML")
                return False

            # Create task
            create_resp = await client.post(
                "https://api.capsolver.com/createTask",
                json={"clientKey": CAPTCHA_API_KEY, "task": task},
            )
            create_resp.raise_for_status()
            task_id = create_resp.json().get("taskId")
            if not task_id:
                logger.warning("CapSolver task creation failed: %s", create_resp.text[:200])
                return False

            # Poll for result (up to 90s)
            import asyncio as _asyncio
            for _ in range(30):
                await _asyncio.sleep(3)
                result_resp = await client.post(
                    "https://api.capsolver.com/getTaskResult",
                    json={"clientKey": CAPTCHA_API_KEY, "taskId": task_id},
                )
                result = result_resp.json()
                if result.get("status") == "ready":
                    token = result["solution"].get("gRecaptchaResponse") or result["solution"].get("gRecaptchaResponse")
                    if token:
                        # Inject token into page
                        await page.evaluate(f"""
                            (function(){{
                                var el = document.getElementById('g-recaptcha-response') ||
                                         document.querySelector('[name="h-captcha-response"]') ||
                                         document.querySelector('[name="g-recaptcha-response"]');
                                if (el) {{ el.value = '{token}'; }}
                                // Trigger callbacks registered by the CAPTCHA widget
                                if (window.captchaCallback) window.captchaCallback('{token}');
                                if (window.onCaptchaSuccess) window.onCaptchaSuccess('{token}');
                            }})()
                        """)
                        logger.info("CAPTCHA token injected successfully")
                        return True
                elif result.get("status") == "failed":
                    logger.warning("CapSolver task failed: %s", result.get("errorDescription"))
                    return False

            logger.warning("CAPTCHA solve timed out")
            return False

    except Exception as exc:
        logger.error("CAPTCHA solve error: %s", exc)
        return False


async def _submit_form(page, analysis: dict) -> None:
    """Click the submit button."""
    submit_text = analysis.get("submit_text", "").strip()
    selectors = []
    if submit_text:
        selectors.append(f'button:has-text("{submit_text}")')
        selectors.append(f'input[value="{submit_text}"]')
    selectors += [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Apply")',
        'button:has-text("Submit")',
        'button:has-text("Send")',
    ]

    for sel in selectors:
        try:
            if await page.locator(sel).count() > 0:
                await page.click(sel)
                logger.info("Submitted form via: %s", sel)
                return
        except Exception:
            continue

    raise RuntimeError("Could not find a submit button on the form")
