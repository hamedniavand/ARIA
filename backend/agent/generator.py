"""Gemini-powered cover letter and document summarisation via REST proxy."""
import logging

import httpx

from core.config import GEMINI_API_KEY, GEMINI_PROXY_URL, GEMINI_MODEL

logger = logging.getLogger(__name__)

_DOC_LABELS = {
    "cv": "curriculum vitae",
    "sop": "statement of purpose",
    "reference": "reference letter",
    "portfolio": "research portfolio",
}


async def _gemini(prompt: str) -> str:
    """Send a prompt to Gemini via the Cloudflare proxy and return the text."""
    url = f"{GEMINI_PROXY_URL}/v1beta/models/{GEMINI_MODEL}:generateContent"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={"X-goog-api-key": GEMINI_API_KEY},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()


async def generate_cover_letter(position, applicant, docs: list) -> str:
    """Return a tailored academic cover letter (plain text, no signature block)."""
    cv = _doc_summary(docs, "cv")
    sop = _doc_summary(docs, "sop")

    prompt = f"""Write a tailored PhD application cover letter.

POSITION:
Title: {position.title}
University: {position.university}, {position.country}
Research description:
{position.description[:2000]}

APPLICANT:
Name: {applicant.name}
Email: {applicant.email}
Field: {applicant.field_of_study}
Background: {applicant.bio[:600]}
CV summary: {cv[:800]}
Statement of purpose: {sop[:500]}

Rules:
- 400-600 words, formal academic tone
- Reference specific research themes or lab focus from the job description
- Show concrete alignment between applicant background and position requirements
- Do NOT open with "I am writing to express my interest"
- Do NOT use placeholder brackets like [Lab Name]
- Output the letter body only — no subject line, no "Dear..." header, no signature"""

    return await _gemini(prompt)


async def summarize_document(text: str, doc_type: str) -> str:
    """Summarise an uploaded document for use in matching and generation."""
    label = _DOC_LABELS.get(doc_type, doc_type)

    prompt = f"""Summarise this {label} for PhD application matching.
Extract: research experience, key skills, programming languages, publications, academic background, and notable achievements.
150-250 words. Output only the summary.

DOCUMENT:
{text[:4000]}"""

    try:
        return await _gemini(prompt)
    except Exception as exc:
        logger.error("summarize_document failed: %s", exc)
        return ""


def _doc_summary(docs: list, doc_type: str) -> str:
    for d in docs:
        if d.doc_type == doc_type and d.summary:
            return d.summary
    return "Not provided"
