import os
import uuid
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from pydantic import BaseModel
from sqlmodel import Session, select
import aiofiles

from core.database import get_session
from core.config import UPLOADS_DIR
from models.applicant import Applicant, Document, ChecklistItem
from models.portal_credential import PortalCredential

router = APIRouter()


# ── Update schemas ────────────────────────────────────────────────────────────

class ApplicantUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    field_of_study: Optional[str] = None
    bio: Optional[str] = None
    preferred_language: Optional[str] = None


class ChecklistItemIn(BaseModel):
    text: str


class ChecklistItemUpdate(BaseModel):
    done: Optional[bool] = None
    text: Optional[str] = None


class CredentialIn(BaseModel):
    portal_domain: str
    username: str
    password: str
    notes: str = ""


# ── Applicants ────────────────────────────────────────────────────────────────

@router.get("", response_model=List[Applicant])
def list_applicants(session: Session = Depends(get_session)):
    return session.exec(select(Applicant).order_by(Applicant.name)).all()


@router.post("", response_model=Applicant, status_code=201)
def create_applicant(
    applicant: Applicant,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    applicant.id = None  # ensure DB assigns it
    session.add(applicant)
    session.commit()
    session.refresh(applicant)
    # Immediately match this new applicant against all existing positions
    background_tasks.add_task(_match_new_applicant, applicant.id)
    return applicant


@router.get("/{applicant_id}", response_model=Applicant)
def get_applicant(applicant_id: int, session: Session = Depends(get_session)):
    a = session.get(Applicant, applicant_id)
    if not a:
        raise HTTPException(status_code=404, detail="Applicant not found")
    return a


_PROFILE_FIELDS = {"bio", "field_of_study", "preferred_language"}


@router.patch("/{applicant_id}", response_model=Applicant)
def update_applicant(
    applicant_id: int,
    data: ApplicantUpdate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    a = session.get(Applicant, applicant_id)
    if not a:
        raise HTTPException(status_code=404, detail="Applicant not found")
    changed = data.model_dump(exclude_none=True)
    profile_changed = bool(changed.keys() & _PROFILE_FIELDS)
    for field, value in changed.items():
        setattr(a, field, value)
    session.add(a)
    session.commit()
    session.refresh(a)
    if profile_changed:
        background_tasks.add_task(_regenerate_covers_for_applicant, applicant_id)
    return a


@router.delete("/{applicant_id}", status_code=204)
def delete_applicant(applicant_id: int, session: Session = Depends(get_session)):
    a = session.get(Applicant, applicant_id)
    if not a:
        raise HTTPException(status_code=404, detail="Applicant not found")
    session.delete(a)
    session.commit()


# ── Documents ─────────────────────────────────────────────────────────────────

@router.get("/{applicant_id}/documents", response_model=List[Document])
def list_documents(applicant_id: int, session: Session = Depends(get_session)):
    return session.exec(
        select(Document).where(Document.applicant_id == applicant_id)
    ).all()


@router.post("/{applicant_id}/documents", response_model=Document, status_code=201)
async def upload_document(
    applicant_id: int,
    background_tasks: BackgroundTasks,
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    if not session.get(Applicant, applicant_id):
        raise HTTPException(status_code=404, detail="Applicant not found")

    ext = os.path.splitext(file.filename or "file")[1] or ".bin"
    unique_name = f"{uuid.uuid4().hex}{ext}"
    dest_dir = os.path.join(UPLOADS_DIR, str(applicant_id))
    os.makedirs(dest_dir, exist_ok=True)
    file_path = os.path.join(dest_dir, unique_name)

    content = await file.read()
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    doc = Document(
        applicant_id=applicant_id,
        doc_type=doc_type,
        filename=file.filename or unique_name,
        file_path=file_path,
        summary="",
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)

    background_tasks.add_task(_index_document, doc.id, file_path, doc_type)
    return doc


@router.delete("/{applicant_id}/documents/{doc_id}", status_code=204)
def delete_document(
    applicant_id: int, doc_id: int, session: Session = Depends(get_session)
):
    doc = session.get(Document, doc_id)
    if not doc or doc.applicant_id != applicant_id:
        raise HTTPException(status_code=404, detail="Document not found")
    if os.path.exists(doc.file_path):
        os.remove(doc.file_path)
    session.delete(doc)
    session.commit()


async def _index_document(doc_id: int, file_path: str, doc_type: str) -> None:
    """Background task: extract text → summarise with Gemini → save summary."""
    from core.database import engine
    from agent.generator import summarize_document
    from sqlmodel import Session as S

    text = _extract_text(file_path)
    if not text.strip():
        return

    summary = await summarize_document(text, doc_type)

    applicant_id = None
    with S(engine) as s:
        doc = s.get(Document, doc_id)
        if doc:
            doc.summary = summary
            applicant_id = doc.applicant_id
            s.add(doc)
            s.commit()

    if applicant_id:
        await _regenerate_covers_for_applicant(applicant_id)


async def _regenerate_covers_for_applicant(applicant_id: int) -> None:
    """Re-generate cover letters for all 'ready' applications of this applicant."""
    from core.database import engine
    from models.application import Application, ApplicationStatus
    from agent.matcher import prepare_application
    from sqlmodel import Session as S, select

    with S(engine) as s:
        app_ids = [
            a.id for a in s.exec(
                select(Application)
                .where(Application.applicant_id == applicant_id)
                .where(Application.status == ApplicationStatus.ready)
            ).all()
        ]

    for app_id in app_ids:
        await prepare_application(app_id)


async def _match_new_applicant(applicant_id: int) -> None:
    """Match a newly created applicant against all existing positions."""
    from agent.matcher import run_matching_for_applicant
    await run_matching_for_applicant(applicant_id)


def _extract_text(file_path: str) -> str:
    if file_path.lower().endswith(".pdf"):
        try:
            import pypdf
            with open(file_path, "rb") as f:
                reader = pypdf.PdfReader(f)
                return "\n".join(p.extract_text() or "" for p in reader.pages)
        except Exception:
            return ""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


# ── Manual re-match trigger ───────────────────────────────────────────────────

@router.post("/{applicant_id}/match")
def trigger_matching(
    applicant_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    """Trigger matching of this applicant against all positions they haven't been scored for."""
    a = session.get(Applicant, applicant_id)
    if not a:
        raise HTTPException(status_code=404, detail="Applicant not found")
    background_tasks.add_task(_match_new_applicant, applicant_id)
    return {"status": "matching started", "applicant_id": applicant_id}


# ── Applicant viewed — reset new_matches_count ───────────────────────────────

@router.post("/{applicant_id}/viewed", status_code=204)
def mark_viewed(applicant_id: int, session: Session = Depends(get_session)):
    """Call when the user opens the applicant's profile — resets the new-match badge."""
    a = session.get(Applicant, applicant_id)
    if a:
        a.new_matches_count = 0
        session.add(a)
        session.commit()


# ── Applicant overview ────────────────────────────────────────────────────────

@router.get("/{applicant_id}/overview")
def get_overview(applicant_id: int, session: Session = Depends(get_session)):
    """Return counts and per-status breakdown for an applicant's applications."""
    from models.application import Application, ApplicationStatus
    a = session.get(Applicant, applicant_id)
    if not a:
        raise HTTPException(status_code=404, detail="Applicant not found")

    apps = session.exec(
        select(Application).where(Application.applicant_id == applicant_id)
    ).all()

    by_status: dict = {}
    for app in apps:
        by_status[app.status] = by_status.get(app.status, 0) + 1

    matched   = sum(1 for ap in apps if ap.status not in (ApplicationStatus.skipped,))
    skipped   = by_status.get(ApplicationStatus.skipped, 0)
    ready     = by_status.get(ApplicationStatus.ready, 0)
    submitted = by_status.get(ApplicationStatus.submitted, 0) + by_status.get(ApplicationStatus.confirmed, 0)
    errors    = by_status.get(ApplicationStatus.error, 0)

    # Top matches by priority_score
    top = sorted(
        [ap for ap in apps if ap.status not in (ApplicationStatus.skipped,)],
        key=lambda x: x.priority_score or x.match_score,
        reverse=True,
    )[:5]

    return {
        "applicant_id": applicant_id,
        "last_matched_at": a.last_matched_at.isoformat() if a.last_matched_at else None,
        "new_matches_count": a.new_matches_count or 0,
        "total_evaluated": len(apps),
        "total_matched": matched,
        "total_skipped": skipped,
        "ready": ready,
        "submitted": submitted,
        "errors": errors,
        "by_status": {k: v for k, v in by_status.items()},
        "top_matches": [
            {
                "application_id": ap.id,
                "position_id": ap.position_id,
                "match_score": ap.match_score,
                "priority_score": ap.priority_score,
                "status": ap.status,
                "reason": ap.error_message,
            }
            for ap in top
        ],
    }


# ── Checklist ─────────────────────────────────────────────────────────────────

@router.get("/{applicant_id}/checklist", response_model=List[ChecklistItem])
def list_checklist(applicant_id: int, session: Session = Depends(get_session)):
    return session.exec(
        select(ChecklistItem)
        .where(ChecklistItem.applicant_id == applicant_id)
        .order_by(ChecklistItem.created_at)
    ).all()


@router.post("/{applicant_id}/checklist", response_model=ChecklistItem, status_code=201)
def add_checklist_item(
    applicant_id: int,
    data: ChecklistItemIn,
    session: Session = Depends(get_session),
):
    if not session.get(Applicant, applicant_id):
        raise HTTPException(status_code=404, detail="Applicant not found")
    item = ChecklistItem(applicant_id=applicant_id, text=data.text.strip())
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@router.patch("/{applicant_id}/checklist/{item_id}", response_model=ChecklistItem)
def update_checklist_item(
    applicant_id: int,
    item_id: int,
    data: ChecklistItemUpdate,
    session: Session = Depends(get_session),
):
    item = session.get(ChecklistItem, item_id)
    if not item or item.applicant_id != applicant_id:
        raise HTTPException(status_code=404, detail="Item not found")
    if data.done is not None:
        item.done = data.done
    if data.text is not None:
        item.text = data.text.strip()
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@router.delete("/{applicant_id}/checklist/{item_id}", status_code=204)
def delete_checklist_item(
    applicant_id: int,
    item_id: int,
    session: Session = Depends(get_session),
):
    item = session.get(ChecklistItem, item_id)
    if not item or item.applicant_id != applicant_id:
        raise HTTPException(status_code=404, detail="Item not found")
    session.delete(item)
    session.commit()


# ── Tailored CV ───────────────────────────────────────────────────────────────

@router.post("/{applicant_id}/applications/{application_id}/tailored-cv")
async def generate_tailored_cv(
    applicant_id: int,
    application_id: int,
    session: Session = Depends(get_session),
):
    from models.application import Application
    from models.position import Position
    from agent.generator import generate_tailored_cv as _gen_cv

    app = session.get(Application, application_id)
    if not app or app.applicant_id != applicant_id:
        raise HTTPException(status_code=404, detail="Application not found")

    a = session.get(Applicant, applicant_id)
    position = session.get(Position, app.position_id)
    docs = session.exec(select(Document).where(Document.applicant_id == applicant_id)).all()

    cv_text = await _gen_cv(position, a, list(docs))
    if not cv_text:
        raise HTTPException(status_code=500, detail="CV generation failed")

    app.tailored_cv = cv_text
    session.add(app)
    session.commit()
    return {"tailored_cv": cv_text}


# ── Analytics ─────────────────────────────────────────────────────────────────

@router.get("/{applicant_id}/analytics")
def get_analytics(applicant_id: int, session: Session = Depends(get_session)):
    """Return timeline + funnel data for Chart.js rendering."""
    from models.application import Application, ApplicationStatus
    from collections import defaultdict

    a = session.get(Applicant, applicant_id)
    if not a:
        raise HTTPException(status_code=404, detail="Applicant not found")

    apps = session.exec(
        select(Application).where(Application.applicant_id == applicant_id)
    ).all()

    # Funnel: matched → ready → submitted
    funnel = {
        "Matched":   sum(1 for ap in apps if ap.status != ApplicationStatus.skipped),
        "Ready":     sum(1 for ap in apps if ap.status in (ApplicationStatus.ready, ApplicationStatus.submitted, ApplicationStatus.confirmed)),
        "Submitted": sum(1 for ap in apps if ap.status in (ApplicationStatus.submitted, ApplicationStatus.confirmed)),
    }

    # Timeline: count of applications created per day (last 60 days)
    daily: dict = defaultdict(int)
    for ap in apps:
        if ap.created_at:
            day = ap.created_at.strftime("%Y-%m-%d")
            daily[day] += 1
    timeline = [{"date": d, "count": c} for d, c in sorted(daily.items())[-60:]]

    # Score distribution buckets
    score_buckets = {"90-100": 0, "75-89": 0, "55-74": 0, "below 55": 0}
    for ap in apps:
        if ap.status == ApplicationStatus.skipped:
            continue
        s = ap.match_score
        if s >= 90:    score_buckets["90-100"] += 1
        elif s >= 75:  score_buckets["75-89"] += 1
        elif s >= 55:  score_buckets["55-74"] += 1
        else:          score_buckets["below 55"] += 1

    return {
        "applicant_id": applicant_id,
        "funnel": funnel,
        "timeline": timeline,
        "score_distribution": score_buckets,
    }


# ── Portal Credentials ────────────────────────────────────────────────────────

@router.get("/{applicant_id}/credentials", response_model=List[PortalCredential])
def list_credentials(applicant_id: int, session: Session = Depends(get_session)):
    return session.exec(
        select(PortalCredential).where(PortalCredential.applicant_id == applicant_id)
    ).all()


@router.post("/{applicant_id}/credentials", response_model=PortalCredential, status_code=201)
def add_credential(
    applicant_id: int,
    data: CredentialIn,
    session: Session = Depends(get_session),
):
    if not session.get(Applicant, applicant_id):
        raise HTTPException(status_code=404, detail="Applicant not found")
    cred = PortalCredential(applicant_id=applicant_id, **data.model_dump())
    session.add(cred)
    session.commit()
    session.refresh(cred)
    return cred


@router.delete("/{applicant_id}/credentials/{cred_id}", status_code=204)
def delete_credential(
    applicant_id: int, cred_id: int, session: Session = Depends(get_session)
):
    cred = session.get(PortalCredential, cred_id)
    if not cred or cred.applicant_id != applicant_id:
        raise HTTPException(status_code=404, detail="Credential not found")
    session.delete(cred)
    session.commit()
