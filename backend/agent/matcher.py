"""Gemini-powered match scoring and application preparation via REST proxy."""
import json
import logging
from typing import Tuple

import httpx

from core.config import GEMINI_API_KEY, GEMINI_PROXY_URL, GEMINI_MODEL
from models.application import Application, ApplicationStatus

logger = logging.getLogger(__name__)

MATCH_THRESHOLD = 70.0  # Lower to show matches; raise to 80+ in production


async def _gemini(prompt: str) -> str:
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

        for applicant in applicants:
            if session.exec(
                select(Application)
                .where(Application.position_id == position_id)
                .where(Application.applicant_id == applicant.id)
            ).first():
                continue

            docs = session.exec(
                select(Document).where(Document.applicant_id == applicant.id)
            ).all()

            score, reason = await _score_match(position, applicant, list(docs))

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

            app = Application(
                position_id=position_id,
                applicant_id=applicant.id,
                match_score=score,
                status=ApplicationStatus.matched,
                error_message=reason,
            )
            session.add(app)
            session.commit()
            session.refresh(app)
            logger.info("Position %s / Applicant %s: matched (%.0f%%)", position_id, applicant.id, score)

        matched = session.exec(
            select(Application)
            .where(Application.position_id == position_id)
            .where(Application.status == ApplicationStatus.matched)
        ).all()
        matched_ids = [a.id for a in matched]

    for app_id in matched_ids:
        await prepare_application(app_id)


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


async def _score_match(position, applicant, docs: list) -> Tuple[float, str]:
    cv = _doc_summary(docs, "cv")
    sop = _doc_summary(docs, "sop")

    prompt = f"""You evaluate PhD application fit. Return ONLY valid JSON — no markdown, no extra text.

POSITION:
Title: {position.title}
University: {position.university}, {position.country}
Description: {position.description[:1500]}

APPLICANT:
Name: {applicant.name}
Field: {applicant.field_of_study}
Bio: {applicant.bio[:500]}
CV: {cv[:600]}
SOP: {sop[:400]}

Score 0-100 (field alignment 40%, skills 30%, research interest 30%).
JSON format: {{"score": <int>, "reason": "<one sentence>"}}"""

    try:
        text = await _gemini(prompt)
        text = text.lstrip("```json").lstrip("```").rstrip("```").strip()
        data = json.loads(text)
        return float(data["score"]), data.get("reason", "")
    except Exception as exc:
        logger.error("Score match error: %s", exc)
        return 0.0, f"Scoring error: {exc}"


def _doc_summary(docs: list, doc_type: str) -> str:
    for d in docs:
        if d.doc_type == doc_type and d.summary:
            return d.summary
    return "Not provided"
