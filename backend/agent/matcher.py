"""Gemini-powered match scoring and application preparation via REST proxy."""
import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Tuple

import httpx

from core.config import GEMINI_API_KEY, GEMINI_PROXY_URL, GEMINI_MODEL
from models.application import Application, ApplicationStatus

logger = logging.getLogger(__name__)

MATCH_THRESHOLD = 55.0  # positions scoring below this are skipped
_SCORING_ERROR_SENTINEL = -1.0  # returned when Gemini call itself fails


async def _gemini(prompt: str, retries: int = 5) -> str:
    """Call Gemini with smart backoff on 429, reading retry-after from error body."""
    url = f"{GEMINI_PROXY_URL}/v1beta/models/{GEMINI_MODEL}:generateContent"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    delay = 16.0  # Gemini free tier says ~15s retry
    for attempt in range(retries):
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"X-goog-api-key": GEMINI_API_KEY},
            )
            if resp.status_code == 429:
                if attempt >= retries - 1:
                    break
                # Try to read the recommended wait from the error body
                wait = delay
                try:
                    body = resp.json()
                    msg = body.get("error", {}).get("message", "")
                    m = re.search(r"retry in ([\d.]+)s", msg)
                    if m:
                        wait = float(m.group(1)) + 2.0  # add 2s buffer
                except Exception:
                    pass
                logger.warning("Gemini 429 rate-limit, retry %d/%d in %.0fs", attempt + 1, retries, wait)
                await asyncio.sleep(wait)
                delay = min(delay * 1.5, 60.0)
                continue
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    raise RuntimeError("Gemini 429 rate-limit persists after retries — try again later")


def _extract_json(text: str) -> dict:
    """Robustly extract a JSON object from Gemini output (strips markdown fences)."""
    text = text.strip()
    # Strip markdown code fences
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    text = text.strip()
    # Find first { ... } block
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return json.loads(m.group())
    return json.loads(text)


async def run_matching_for_applicant(applicant_id: int) -> None:
    """Score one applicant against ALL existing positions they haven't been evaluated for yet."""
    from sqlmodel import Session, select
    from core.database import engine
    from models.position import Position
    from models.applicant import Applicant, Document

    with Session(engine) as session:
        applicant = session.get(Applicant, applicant_id)
        if not applicant:
            return
        # All positions not yet evaluated for this applicant
        already_scored = {
            row.position_id for row in session.exec(
                select(Application).where(Application.applicant_id == applicant_id)
            ).all()
        }
        positions = [
            p for p in session.exec(select(Position)).all()
            if p.id not in already_scored
        ]
        docs = list(session.exec(select(Document).where(Document.applicant_id == applicant_id)).all())
        logger.info("Matching applicant %s against %d unscored positions", applicant.name, len(positions))

    # Bulk-insert pre-filter skips (no Gemini needed)
    prefilter_skips = [p for p in positions if _is_obvious_mismatch(p, applicant)]
    gemini_positions = [p for p in positions if not _is_obvious_mismatch(p, applicant)]

    if prefilter_skips:
        with Session(engine) as session:
            for p in prefilter_skips:
                session.add(Application(
                    position_id=p.id,
                    applicant_id=applicant_id,
                    match_score=10.0,
                    status=ApplicationStatus.skipped,
                    error_message="Field mismatch (pre-filter)",
                ))
            session.commit()
        logger.info("Pre-filtered %d positions for applicant %s", len(prefilter_skips), applicant.name)

    logger.info("Scoring %d positions with Gemini for applicant %s", len(gemini_positions), applicant.name)

    # Score remaining positions concurrently (8 at a time to stay within rate limits)
    CONCURRENCY = 8
    new_match_count = 0

    async def _score_and_store(position) -> None:
        nonlocal new_match_count
        score, reason, breakdown = await _score_match(position, applicant, docs)
        if score == _SCORING_ERROR_SENTINEL:
            logger.warning("Scoring error pos=%s appl=%s", position.id, applicant_id)
            return
        with Session(engine) as session:
            if score < MATCH_THRESHOLD:
                session.add(Application(
                    position_id=position.id,
                    applicant_id=applicant_id,
                    match_score=score,
                    status=ApplicationStatus.skipped,
                    error_message=reason,
                ))
                session.commit()
                return
            priority = _priority_score(score, position.deadline)
            app = Application(
                position_id=position.id,
                applicant_id=applicant_id,
                match_score=score,
                priority_score=priority,
                match_breakdown=json.dumps(breakdown),
                status=ApplicationStatus.matched,
                error_message=reason,
            )
            session.add(app)
            session.commit()
            session.refresh(app)
            app_id = app.id
            new_match_count += 1
            logger.info("Matched pos=%s appl=%s score=%.0f%%", position.id, applicant_id, score)

        # Generate cover letter immediately so restarts don't lose progress
        # (startup hook _resume_pending_preparations picks up any that were interrupted)
        try:
            await prepare_application(app_id)
        except Exception as exc:
            logger.error("prepare_application %s failed: %s", app_id, exc)

    for i in range(0, len(gemini_positions), CONCURRENCY):
        batch = gemini_positions[i:i + CONCURRENCY]
        await asyncio.gather(*[_score_and_store(p) for p in batch])

    # Update last_matched_at and badge count
    if new_match_count:
        with Session(engine) as session:
            appl = session.get(Applicant, applicant_id)
            if appl:
                appl.last_matched_at = datetime.utcnow()
                appl.new_matches_count = (appl.new_matches_count or 0) + new_match_count
                session.add(appl)
                session.commit()
        logger.info("Applicant %s: %d new matches, cover letters generated", applicant_id, new_match_count)


async def run_matching_for_position(position_id: int) -> None:
    """Score every active applicant against a position; create Application rows."""
    from sqlmodel import Session, select
    from core.database import engine
    from models.position import Position
    from models.applicant import Applicant, Document

    with Session(engine) as session:
        position = session.get(Position, position_id)
        if not position:
            return
        applicants = session.exec(select(Applicant)).all()

        matched_app_ids = []
        for applicant in applicants:
            # Skip if already evaluated (any status except error)
            existing = session.exec(
                select(Application)
                .where(Application.position_id == position_id)
                .where(Application.applicant_id == applicant.id)
            ).first()
            if existing and existing.status != ApplicationStatus.error:
                continue

            docs = session.exec(
                select(Document).where(Document.applicant_id == applicant.id)
            ).all()

            # Fast pre-filter: skip obvious field mismatches without calling Gemini
            if _is_obvious_mismatch(position, applicant):
                session.add(Application(
                    position_id=position_id,
                    applicant_id=applicant.id,
                    match_score=10.0,
                    status=ApplicationStatus.skipped,
                    error_message="Field mismatch (pre-filter)",
                ))
                session.commit()
                continue

            score, reason, breakdown = await _score_match(position, applicant, list(docs))

            # Scoring infrastructure error — don't treat as a real 0; skip silently
            # and let a re-scan try again
            if score == _SCORING_ERROR_SENTINEL:
                logger.warning("Position %s / Applicant %s: scoring error, leaving unscored", position_id, applicant.id)
                continue

            if score < MATCH_THRESHOLD:
                session.add(Application(
                    position_id=position_id,
                    applicant_id=applicant.id,
                    match_score=score,
                    status=ApplicationStatus.skipped,
                    error_message=reason,
                ))
                session.commit()
                logger.info("Position %s / Applicant %s: skipped (%.0f%%)", position_id, applicant.id, score)
                continue

            priority = _priority_score(score, position.deadline)
            app = Application(
                position_id=position_id,
                applicant_id=applicant.id,
                match_score=score,
                priority_score=priority,
                match_breakdown=json.dumps(breakdown),
                status=ApplicationStatus.matched,
                error_message=reason,
            )
            session.add(app)
            session.commit()
            session.refresh(app)
            matched_app_ids.append(app.id)
            logger.info("Position %s / Applicant %s: matched (%.0f%%, priority=%.1f)",
                        position_id, applicant.id, score, priority)

            # Update applicant: bump new_matches_count + last_matched_at
            appl = session.get(Applicant, applicant.id)
            if appl:
                appl.last_matched_at = datetime.utcnow()
                appl.new_matches_count = (appl.new_matches_count or 0) + 1
                session.add(appl)
                session.commit()

    # Prepare cover letters for all newly matched applications
    for app_id in matched_app_ids:
        try:
            await prepare_application(app_id)
        except Exception as exc:
            logger.error("prepare_application %s failed: %s", app_id, exc)


async def prepare_application(application_id: int) -> None:
    """Generate cover letter → move application to 'ready'."""
    from sqlmodel import Session, select
    from core.database import engine
    from models.position import Position
    from models.applicant import Applicant, Document
    from agent.generator import generate_cover_letter

    with Session(engine) as session:
        app = session.get(Application, application_id)
        if not app:
            return
        app.status = ApplicationStatus.preparing
        session.add(app)
        session.commit()

        position = session.get(Position, app.position_id)
        applicant = session.get(Applicant, app.applicant_id)
        docs = session.exec(
            select(Document).where(Document.applicant_id == app.applicant_id)
        ).all()

    try:
        cover_letter = await generate_cover_letter(position, applicant, list(docs))
        with Session(engine) as session:
            app = session.get(Application, application_id)
            app.cover_letter = cover_letter
            app.status = ApplicationStatus.ready
            session.add(app)
            session.commit()
        logger.info("Application %s ready", application_id)

    except Exception as exc:
        logger.error("prepare_application %s failed: %s", application_id, exc)
        with Session(engine) as session:
            app = session.get(Application, application_id)
            app.status = ApplicationStatus.error
            app.error_message = str(exc)
            session.add(app)
            session.commit()


def _priority_score(match_score: float, deadline_str: str | None) -> float:
    """
    Combined priority = match score weighted by deadline urgency.
    Deadline within 7 days → 2× boost; within 30 days → 1.5×; beyond → 1×.
    """
    urgency = 1.0
    if deadline_str:
        try:
            from dateutil import parser as dp
            deadline = dp.parse(deadline_str, fuzzy=True)
            days_left = (deadline - datetime.utcnow()).days
            if days_left <= 0:
                urgency = 0.5       # already passed
            elif days_left <= 7:
                urgency = 2.0
            elif days_left <= 14:
                urgency = 1.75
            elif days_left <= 30:
                urgency = 1.5
            elif days_left <= 60:
                urgency = 1.2
        except Exception:
            pass
    return round(match_score * urgency, 1)


async def _score_match(position, applicant, docs: list) -> Tuple[float, str, dict]:
    cv_full  = _doc_full_text(docs, "cv")
    sop_full = _doc_full_text(docs, "sop")
    cv_sum   = _doc_summary(docs, "cv")
    sop_sum  = _doc_summary(docs, "sop")

    # Prefer full text, fall back to summary
    cv_text  = cv_full[:3000]  if cv_full  else cv_sum[:1000]
    sop_text = sop_full[:1500] if sop_full else sop_sum[:600]

    # Also pull any portfolio/reference summaries as supplementary context
    extra_docs = []
    for d in docs:
        if d.doc_type not in ("cv", "sop") and d.summary:
            extra_docs.append(f"[{d.doc_type}]: {d.summary[:400]}")
    extra_text = "\n".join(extra_docs)

    # Build the applicant info block — be explicit about what's available
    has_cv  = bool(cv_text.strip())
    has_sop = bool(sop_text.strip())
    applicant_block = f"""Name: {applicant.name}
Field of study: {applicant.field_of_study}
Research interests / Bio:
{applicant.bio[:800]}
"""
    if has_cv:
        applicant_block += f"\nCV:\n{cv_text}\n"
    else:
        applicant_block += "\n[No CV uploaded — evaluate based on bio and field only]\n"

    if has_sop:
        applicant_block += f"\nStatement of Purpose:\n{sop_text}\n"

    if extra_text:
        applicant_block += f"\nOther documents:\n{extra_text}\n"

    prompt = f"""You are an expert PhD admissions evaluator. Score how well this applicant fits the position.

IMPORTANT CALIBRATION:
- Judge purely on field/skill match, NOT on document completeness.
- If no CV is uploaded, score based on bio and field_of_study — do NOT penalise for missing documents.
- Score 70+ means genuinely strong field/skill overlap.
- Score 50-69 means partial match, worth considering.
- Score below 50 means clearly different field or missing skills.
- Even with minimal info, give a realistic score based on field alignment alone.

=== POSITION ===
Title: {position.title}
University: {position.university or "Unknown"}, {position.country or "Unknown"}
Field: {position.field or "Not specified"}
Description:
{(position.description or "No description available")[:2000]}

=== APPLICANT ===
{applicant_block}

=== TASK ===
Evaluate on these four dimensions (each 0-100):
1. field_alignment (weight 35%) — Does the applicant's field of study match the position's research area?
2. skills_match (weight 25%) — Do the applicant's technical skills/methods match what the position requires?
3. research_fit (weight 25%) — Does the applicant's research experience/interests align with the lab/project?
4. profile_strength (weight 15%) — Overall academic profile strength for a PhD position.

Return ONLY valid JSON, no markdown fences, no text outside the JSON:
{{
  "field_alignment": <int 0-100>,
  "skills_match": <int 0-100>,
  "research_fit": <int 0-100>,
  "profile_strength": <int 0-100>,
  "overall": <int 0-100>,
  "reason": "<2-3 sentence explanation>"
}}"""

    try:
        text = await _gemini(prompt)
        data = _extract_json(text)
        # Compute weighted overall if not provided or to cross-check
        breakdown = {
            "field_alignment": int(data.get("field_alignment", 0)),
            "skills_match":    int(data.get("skills_match", 0)),
            "research_fit":    int(data.get("research_fit", 0)),
            "profile_strength": int(data.get("profile_strength", 0)),
        }
        # Use Gemini's overall score; fall back to weighted average
        overall = float(data.get("overall", 0))
        if overall == 0:
            overall = (
                breakdown["field_alignment"] * 0.35 +
                breakdown["skills_match"]    * 0.25 +
                breakdown["research_fit"]    * 0.25 +
                breakdown["profile_strength"]* 0.15
            )
        reason = data.get("reason", "")
        return round(overall, 1), reason, breakdown

    except Exception as exc:
        logger.error("Score match error: %s | raw: %.200s", exc, locals().get("text", ""))
        return _SCORING_ERROR_SENTINEL, f"Scoring error: {exc}", {}


# ── Field groups: positions and applicants in the same group can match ────────
_FIELD_GROUPS = [
    {"Computer Science", "Mathematics", "Engineering"},
    {"Biology", "Medicine & Health", "Environmental Science", "Chemistry"},
    {"Physics", "Mathematics", "Engineering", "Computer Science"},
    {"Economics & Business", "Social Sciences"},
    {"Humanities", "Social Sciences"},
    {"Chemistry", "Biology", "Environmental Science"},
    {"Engineering", "Computer Science", "Physics", "Mathematics"},
    {"Medicine & Health", "Biology"},
]

# Fields that MUST match closely — don't waste Gemini calls on obvious mismatches
_STRICT_FIELDS = {"Humanities", "Medicine & Health", "Economics & Business"}


# Per-field keywords: if the applicant's text contains NONE of these, it's a mismatch
_FIELD_REQUIRED_KEYWORDS = {
    "Computer Science":      ["computer", "software", "programming", "machine learning", "ai", "data science",
                              "algorithm", "neural", "nlp", "deep learning", "coding", "developer", "engineer",
                              "informatics", "computational", "network", "cybersecurity"],
    "Mathematics":           ["math", "statistic", "algebra", "topology", "probability", "calculus",
                              "computational", "quantitative", "numerical"],
    "Physics":               ["physic", "quantum", "optic", "astrophy", "condensed matter", "particle",
                              "plasma", "photon", "spectro"],
    "Chemistry":             ["chemi", "organic", "inorganic", "polymer", "spectro", "molecular", "reaction",
                              "synthesis", "catalyst", "material"],
    "Biology":               ["biology", "biochem", "molecular", "genetic", "genomic", "ecology", "microbio",
                              "cell", "protein", "neurosci", "evolution", "organism"],
    "Engineering":           ["engineer", "mechanical", "electrical", "civil", "aerospace", "robotics",
                              "embedded", "control", "biomedical", "system design", "hardware", "manufacturing"],
    "Medicine & Health":     ["medicine", "medical", "clinical", "pharma", "immunol", "epidemiol", "health",
                              "nursing", "oncol", "pathol", "patient", "drug", "therapy", "diagnosis"],
    "Environmental Science": ["environment", "climate", "ecology", "sustainab", "energy", "atmospheric",
                              "geoscience", "ocean", "carbon", "pollution"],
    "Economics & Business":  ["economics", "business", "finance", "management", "marketing", "accounting",
                              "supply chain", "commerce", "trade", "entrepreneurship"],
    "Social Sciences":       ["psychology", "sociology", "political", "anthropology", "education",
                              "linguistics", "communication", "social", "cognitive", "behavior"],
    "Humanities":            ["history", "philosoph", "literature", "archaeol", "cultural", "language",
                              "law", "humanities", "arts", "museum", "heritage"],
}


def _is_obvious_mismatch(position, applicant) -> bool:
    """
    Return True if the position field has no keyword overlap with the applicant's profile.
    Avoids Gemini calls for clear mismatches.
    """
    pos_field  = (position.field or "").strip()
    appl_text  = ((applicant.field_of_study or "") + " " + (applicant.bio or "")).lower()

    # Unknown position field — can't pre-filter
    if not pos_field or pos_field == "Other":
        return False

    required_kws = _FIELD_REQUIRED_KEYWORDS.get(pos_field)
    if not required_kws:
        return False

    # If applicant text has at least one keyword for this field → not a mismatch
    return not any(kw in appl_text for kw in required_kws)


def _doc_summary(docs: list, doc_type: str) -> str:
    for d in docs:
        if d.doc_type == doc_type and d.summary:
            return d.summary
    return ""


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
                if path.lower().endswith(".docx"):
                    try:
                        from docx import Document as DocxDoc
                        doc = DocxDoc(path)
                        return "\n".join(p.text for p in doc.paragraphs)
                    except Exception:
                        pass
                if path.lower().endswith(".doc"):
                    try:
                        import subprocess
                        result = subprocess.run(
                            ["antiword", path], capture_output=True, text=True, timeout=10
                        )
                        if result.returncode == 0 and result.stdout.strip():
                            return result.stdout
                    except Exception:
                        pass
                    # Fallback: read raw bytes as latin-1, strip non-printable
                    try:
                        with open(path, "rb") as f:
                            raw = f.read().decode("latin-1", errors="replace")
                        # Keep only printable ASCII runs of 4+ chars
                        import re as _re
                        chunks = _re.findall(r'[A-Za-z0-9 ,.\-:;()/\n]{4,}', raw)
                        return " ".join(chunks)[:5000]
                    except Exception:
                        pass
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read()
            except Exception:
                pass
    return ""
