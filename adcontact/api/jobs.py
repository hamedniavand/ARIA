"""Scrape job endpoints — trigger a run, list history."""
import asyncio
import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlmodel import Session, select

from core.database import get_session
from models.contact import ScrapeJob, Contact

router = APIRouter()
logger = logging.getLogger(__name__)


class JobIn(BaseModel):
    niche: str


@router.get("", response_model=List[ScrapeJob])
def list_jobs(session: Session = Depends(get_session)):
    return session.exec(select(ScrapeJob).order_by(ScrapeJob.created_at.desc())).all()


@router.post("", response_model=ScrapeJob, status_code=201)
def create_job(data: JobIn, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    niche = data.niche.strip()
    if not niche:
        raise HTTPException(status_code=400, detail="Niche cannot be empty")
    job = ScrapeJob(niche=niche)
    session.add(job)
    session.commit()
    session.refresh(job)
    background_tasks.add_task(_run_scrape, job.id, niche)
    return job


@router.get("/{job_id}", response_model=ScrapeJob)
def get_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _is_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")


async def _run_scrape(job_id: int, niche: str) -> None:
    """Background task: search → visit → store contacts."""
    from core.database import engine
    from sqlmodel import Session as S
    from scraper.google_search import collect_urls
    from scraper.directory_scraper import scrape_directory_page
    from scraper.page_parser import parse_site

    logger.info("Job %d starting for niche/url: %s", job_id, niche)

    try:
        if _is_url(niche):
            urls = await scrape_directory_page(niche)
        else:
            urls = await collect_urls(niche)

        with S(engine) as s:
            job = s.get(ScrapeJob, job_id)
            job.total_found = len(urls)
            s.add(job)
            s.commit()

        new_contacts = 0

        for i, url in enumerate(urls):
            await asyncio.sleep(1.5)   # polite delay between site visits
            try:
                contact_data = await parse_site(url)
            except Exception as exc:
                logger.debug("parse_site error %s: %s", url, exc)
                continue

            if not contact_data:
                continue

            with S(engine) as s:
                # Skip if domain already in DB (UNIQUE constraint)
                existing = s.exec(
                    select(Contact).where(Contact.website_url == contact_data["website_url"])
                ).first()
                if existing:
                    logger.debug("Duplicate skipped: %s", contact_data["website_url"])
                    continue

                contact = Contact(
                    job_id=job_id,
                    niche=niche,
                    **contact_data,
                )
                s.add(contact)
                try:
                    s.commit()
                    new_contacts += 1
                    logger.info("Saved: %s (%s)", contact_data["website_url"], contact_data["email"])
                except Exception:
                    s.rollback()   # race-condition duplicate

            # Update progress every 10 sites
            if i % 10 == 0:
                with S(engine) as s:
                    job = s.get(ScrapeJob, job_id)
                    job.new_contacts = new_contacts
                    s.add(job)
                    s.commit()

        with S(engine) as s:
            job = s.get(ScrapeJob, job_id)
            job.status = "done"
            job.new_contacts = new_contacts
            job.completed_at = datetime.utcnow()
            s.add(job)
            s.commit()

        logger.info("Job %d done: %d new contacts from %d URLs", job_id, new_contacts, len(urls))

    except Exception as exc:
        logger.error("Job %d failed: %s", job_id, exc)
        from core.database import engine
        from sqlmodel import Session as S
        with S(engine) as s:
            job = s.get(ScrapeJob, job_id)
            if job:
                job.status = "failed"
                job.completed_at = datetime.utcnow()
                s.add(job)
                s.commit()
