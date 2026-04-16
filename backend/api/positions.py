import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Body
from sqlmodel import Session, select

from core.database import get_session
from models.position import Position

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("", response_model=List[Position])
def list_positions(
    source_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    session: Session = Depends(get_session),
):
    q = select(Position)
    if source_id is not None:
        q = q.where(Position.source_id == source_id)
    if search:
        term = f"%{search}%"
        q = q.where(
            Position.title.like(term)
            | Position.university.like(term)
            | Position.country.like(term)
        )
    return session.exec(q.order_by(Position.discovered_at.desc())).all()


@router.delete("/batch", status_code=204)
def batch_delete_positions(
    ids: List[int] = Body(..., embed=True),
    session: Session = Depends(get_session),
):
    """Delete multiple positions (and their applications) by ID."""
    from models.application import Application
    for pos_id in ids:
        # Delete child applications first
        for app in session.exec(
            select(Application).where(Application.position_id == pos_id)
        ).all():
            session.delete(app)
        pos = session.get(Position, pos_id)
        if pos:
            session.delete(pos)
    session.commit()


@router.post("/rematch", status_code=202)
def rematch_all(background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    """Re-run matching for ALL positions against all active applicants."""
    position_ids = [p.id for p in session.exec(select(Position)).all()]
    background_tasks.add_task(_run_rematch, position_ids)
    return {"queued": len(position_ids)}


async def _run_rematch(position_ids: List[int]) -> None:
    from agent.matcher import run_matching_for_position
    from models.application import Application
    from core.database import engine
    from sqlmodel import Session as S

    logger.info("Rematch: clearing existing applications for %d positions", len(position_ids))
    with S(engine) as s:
        for pos_id in position_ids:
            for app in s.exec(select(Application).where(Application.position_id == pos_id)).all():
                s.delete(app)
        s.commit()

    logger.info("Rematch: running matching for %d positions", len(position_ids))
    for pos_id in position_ids:
        try:
            await run_matching_for_position(pos_id)
        except Exception as exc:
            logger.error("Rematch error for position %d: %s", pos_id, exc)
        await asyncio.sleep(0.1)

    logger.info("Rematch complete for %d positions", len(position_ids))


@router.get("/{position_id}", response_model=Position)
def get_position(position_id: int, session: Session = Depends(get_session)):
    position = session.get(Position, position_id)
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    return position
