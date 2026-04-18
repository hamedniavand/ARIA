"""Gemini-powered cover letter, CV tailoring and document summarisation."""
import asyncio
import logging
import re

import httpx

from core.config import GEMINI_API_KEY, GEMINI_PROXY_URL, GEMINI_MODEL

logger = logging.getLogger(__name__)

_DOC_LABELS = {
    "cv": "curriculum vitae",
    "sop": "statement of purpose",
    "reference": "reference letter",
    "portfolio": "research portfolio",
}


async def _gemini(prompt: str, retries: int = 5) -> str:
    """Call Gemini with smart backoff on 429, reading retry-after from error body."""
    import re as _re
    url = f"{GEMINI_PROXY_URL}/v1beta/models/{GEMINI_MODEL}:generateContent"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    delay = 16.0
    for attempt in range(retries):
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"X-goog-api-key": GEMINI_API_KEY},
            )
            if resp.status_code == 429:
                if attempt >= retries - 1:
                    break
                wait = delay
                try:
                    body = resp.json()
                    msg = body.get("error", {}).get("message", "")
                    m = _re.search(r"retry in ([\d.]+)s", msg)
                    if m:
                        wait = float(m.group(1)) + 2.0
                except Exception:
                    pass
                logger.warning("Gemini 429, retry %d/%d in %.0fs", attempt + 1, retries, wait)
                await asyncio.sleep(wait)
                delay = min(delay * 1.5, 60.0)
                continue
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    raise RuntimeError("Gemini 429 rate-limit persists after retries — try again later")


async def generate_cover_letter(position, applicant, docs: list) -> str:
    """Return a tailored academic cover letter (plain text, no signature block)."""
    cv  = _doc_text_or_summary(docs, "cv",  3000)
    sop = _doc_text_or_summary(docs, "sop", 1500)
    language = getattr(applicant, "preferred_language", "English") or "English"

    prompt = f"""Write a tailored PhD application cover letter in {language}.

POSITION:
Title: {position.title}
University: {position.university}, {position.country}
Field: {position.field}
Research description:
{position.description[:2500]}

APPLICANT:
Name: {applicant.name}
Email: {applicant.email}
Field of study: {applicant.field_of_study}
Background: {applicant.bio[:600]}

CV:
{cv}

Statement of Purpose:
{sop}

Rules:
- 450-600 words, formal academic tone, written entirely in {language}
- Reference specific research themes, methods, or lab focus from the job description
- Show concrete alignment between applicant background and position requirements
- Mention relevant skills/techniques by name if they appear in both CV and position
- Do NOT open with "I am writing to express my interest"
- Do NOT use placeholder brackets like [Lab Name] or [Professor Name]
- Output the letter body only — no subject line, no "Dear..." header, no signature"""

    return await _gemini(prompt)


async def generate_tailored_cv(position, applicant, docs: list) -> str:
    """
    Generate a tailored CV for a specific position.
    Restructures and highlights the most relevant sections from the applicant's CV.
    Returns plain text formatted as a professional CV.
    """
    cv_full  = _doc_full_text(docs, "cv")
    sop_full = _doc_full_text(docs, "sop")
    cv_sum   = _doc_summary(docs, "cv")

    cv_content = cv_full[:4000] if cv_full else cv_sum[:1200]
    language = getattr(applicant, "preferred_language", "English") or "English"

    prompt = f"""You are a professional academic CV writer. Create a tailored CV for a PhD application.

TARGET POSITION:
Title: {position.title}
University: {position.university}, {position.country}
Field: {position.field}
Key requirements from description:
{position.description[:1800]}

APPLICANT:
Name: {applicant.name}
Email: {applicant.email}
Field of study: {applicant.field_of_study}
Bio: {applicant.bio[:500]}

ORIGINAL CV CONTENT:
{cv_content}

ADDITIONAL CONTEXT (Statement of Purpose):
{sop_full[:800] if sop_full else ""}

INSTRUCTIONS:
1. Reorder and emphasise sections that are most relevant to this position
2. Lead with a 2-3 line professional summary tailored to this specific position
3. Highlight technical skills, methods, and tools that match the position's requirements
4. Keep all factual information accurate — do not invent qualifications
5. Use clear section headers: SUMMARY, EDUCATION, RESEARCH EXPERIENCE, SKILLS, PUBLICATIONS (if any), OTHER
6. Write in {language}, formal academic tone
7. Output clean plain text — no markdown symbols, no bullet points with *, use – for bullets
8. Length: 1-2 pages equivalent (600-900 words)"""

    try:
        return await _gemini(prompt)
    except Exception as exc:
        logger.error("generate_tailored_cv failed: %s", exc)
        return ""


async def summarize_document(text: str, doc_type: str) -> str:
    """Summarise an uploaded document for use in matching and generation."""
    label = _DOC_LABELS.get(doc_type, doc_type)

    prompt = f"""Summarise this {label} for PhD application matching.
Extract: research experience, key skills, programming languages, publications, academic background, and notable achievements.
200-300 words. Output only the summary, no headers.

DOCUMENT:
{text[:5000]}"""

    try:
        return await _gemini(prompt)
    except Exception as exc:
        logger.error("summarize_document failed: %s", exc)
        return ""


# ── helpers ───────────────────────────────────────────────────────────────────

def _doc_text_or_summary(docs: list, doc_type: str, max_chars: int) -> str:
    full = _doc_full_text(docs, doc_type)
    if full:
        return full[:max_chars]
    return _doc_summary(docs, doc_type)[:max_chars]


def _doc_summary(docs: list, doc_type: str) -> str:
    for d in docs:
        if d.doc_type == doc_type and d.summary:
            return d.summary
    return "Not provided"


def _doc_full_text(docs: list, doc_type: str) -> str:
    for d in docs:
        if d.doc_type == doc_type and getattr(d, "file_path", None):
            try:
                path = d.file_path
                if path.lower().endswith(".pdf"):
                    import pypdf
                    with open(path, "rb") as f:
                        reader = pypdf.PdfReader(f)
                        return "\n".join(p.extract_text() or "" for p in reader.pages)
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read()
            except Exception:
                pass
    return ""
