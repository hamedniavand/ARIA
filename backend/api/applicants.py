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
from models.applicant import Applicant, Document
from models.portal_credential import PortalCredential

router = APIRouter()


# ── Update schemas ────────────────────────────────────────────────────────────

class ApplicantUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    field_of_study: Optional[str] = None
    bio: Optional[str] = None
    preferred_language: Optional[str] = None


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
def create_applicant(applicant: Applicant, session: Session = Depends(get_session)):
    applicant.id = None  # ensure DB assigns it
    session.add(applicant)
    session.commit()
    session.refresh(applicant)
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
