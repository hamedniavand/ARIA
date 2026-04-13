from typing import List, Optional, Any
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlmodel import Session, select

from core.database import get_session
from models.source import Source

router = APIRouter()


class SourceUpdate(BaseModel):
    url: Optional[str] = None
    label: Optional[str] = None
    is_active: Optional[bool] = None


class SourceWithStats(BaseModel):
    id: int
    url: str
    label: str
    is_active: bool
    last_scraped_at: Optional[datetime]
    created_at: Optional[datetime]
    position_count: int
    reliability_score: Optional[float]   # 0–100, null if never scraped


def _reliability(positions) -> Optional[float]:
    """Compute data-completeness score (0–100) across a source's positions."""
    if not positions:
        return None
    total = 0
    for p in positions:
        s = 0
        if p.title and p.title not in ("Unknown Title", ""):
            s += 25
        if p.university:
            s += 25
        if p.country:
            s += 25
        if p.deadline:
            s += 25
        total += s
    return round(total / len(positions), 1)


@router.get("", response_model=List[SourceWithStats])
def list_sources(session: Session = Depends(get_session)):
    from models.position import Position
    sources = session.exec(select(Source).order_by(Source.label)).all()
    result = []
    for src in sources:
        positions = session.exec(
            select(Position).where(Position.source_id == src.id)
        ).all()
        result.append(SourceWithStats(
            id=src.id,
            url=src.url,
            label=src.label,
            is_active=src.is_active,
            last_scraped_at=src.last_scraped_at,
            created_at=src.created_at,
            position_count=len(positions),
            reliability_score=_reliability(positions),
        ))
    return result


@router.post("", response_model=Source, status_code=201)
def create_source(source: Source, session: Session = Depends(get_session)):
    source.id = None
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


@router.patch("/{source_id}", response_model=Source)
def update_source(
    source_id: int,
    data: SourceUpdate,
    session: Session = Depends(get_session),
):
    source = session.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(source, field, value)
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


@router.delete("/{source_id}", status_code=204)
def delete_source(source_id: int, session: Session = Depends(get_session)):
    source = session.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    session.delete(source)
    session.commit()


@router.post("/{source_id}/scan")
def trigger_scan(
    source_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    source = session.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if not source.is_active:
        raise HTTPException(status_code=400, detail="Source is disabled")

    background_tasks.add_task(_run_pipeline, source_id)
    return {"status": "scan started", "source_id": source_id, "label": source.label}


async def _run_pipeline(source_id: int) -> None:
    """Scrape → match → generate cover letters for all new positions."""
    from agent.scraper import scrape_source
    from agent.matcher import run_matching_for_position

    new_ids = await scrape_source(source_id)
    for pos_id in new_ids:
        await run_matching_for_position(pos_id)
