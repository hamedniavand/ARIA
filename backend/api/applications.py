from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, Body
from pydantic import BaseModel
from sqlmodel import Session, select

from core.database import get_session
from models.application import Application, ApplicationStatus

router = APIRouter()


class ApplicationUpdate(BaseModel):
    cover_letter: Optional[str] = None
    status: Optional[ApplicationStatus] = None
    error_message: Optional[str] = None


class BatchStatusUpdate(BaseModel):
    ids: List[int]
    status: ApplicationStatus


@router.get("", response_model=List[Application])
def list_applications(
    status: Optional[ApplicationStatus] = Query(None),
    applicant_id: Optional[int] = Query(None),
    position_id: Optional[int] = Query(None),
    sort: Optional[str] = Query("priority"),   # priority | score | date
    session: Session = Depends(get_session),
):
    q = select(Application)
    if status is not None:
        q = q.where(Application.status == status)
    if applicant_id is not None:
        q = q.where(Application.applicant_id == applicant_id)
    if position_id is not None:
        q = q.where(Application.position_id == position_id)

    results = session.exec(q).all()

    if sort == "priority":
        results = sorted(results, key=lambda x: x.priority_score or x.match_score, reverse=True)
    elif sort == "score":
        results = sorted(results, key=lambda x: x.match_score, reverse=True)
    else:
        results = sorted(results, key=lambda x: x.created_at or datetime.min, reverse=True)

    return results


@router.get("/{application_id}", response_model=Application)
def get_application(application_id: int, session: Session = Depends(get_session)):
    app = session.get(Application, application_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


@router.patch("/batch")
def batch_update_applications(
    data: BatchStatusUpdate,
    session: Session = Depends(get_session),
):
    """Change status of multiple applications at once."""
    updated = 0
    for app_id in data.ids:
        app = session.get(Application, app_id)
        if app:
            app.status = data.status
            if data.status == ApplicationStatus.submitted and not app.submitted_at:
                app.submitted_at = datetime.utcnow()
            session.add(app)
            updated += 1
    session.commit()
    return {"updated": updated}


@router.patch("/{application_id}", response_model=Application)
def update_application(
    application_id: int,
    data: ApplicationUpdate,
    session: Session = Depends(get_session),
):
    app = session.get(Application, application_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(app, field, value)
    session.add(app)
    session.commit()
    session.refresh(app)
    return app


@router.post("/{application_id}/approve", response_model=Application)
def approve_application(
    application_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    """Human approval — triggers Playwright browser agent to submit the application."""
    app = session.get(Application, application_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.status != ApplicationStatus.ready:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve: status is '{app.status}', expected 'ready'",
        )
    # Set to 'preparing' immediately so the UI shows it's in progress
    app.status = ApplicationStatus.preparing
    session.add(app)
    session.commit()
    session.refresh(app)

    background_tasks.add_task(_submit_via_browser, app.id)
    return app


@router.get("/{application_id}/screenshots")
def list_screenshots(application_id: int):
    """Return URLs of screenshots taken for this application."""
    from pathlib import Path
    from core.config import SCREENSHOTS_DIR
    shots_dir = Path(SCREENSHOTS_DIR)
    files = sorted(shots_dir.glob(f"app_{application_id}_*.png"))
    return [{"stage": f.stem.split("_", 2)[-1], "url": f"/screenshots/{f.name}"} for f in files]


@router.post("/{application_id}/retry")
def retry_application(
    application_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    """Reset a failed application and re-queue cover letter generation."""
    app = session.get(Application, application_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.status != ApplicationStatus.error:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot retry: status is '{app.status}', expected 'error'",
        )
    app.status = ApplicationStatus.matched
    app.error_message = ""
    session.add(app)
    session.commit()

    background_tasks.add_task(_prepare_application, app.id)
    return {"status": "queued", "application_id": application_id}


async def _prepare_application(application_id: int) -> None:
    from agent.matcher import prepare_application
    await prepare_application(application_id)


async def _submit_via_browser(application_id: int) -> None:
    from agent.browser import submit_application
    await submit_application(application_id)
