"""Contact list, filter, delete, toggle contacted, CSV export."""
import csv
import io
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from core.database import get_session
from models.contact import Contact

router = APIRouter()

# ── GST window helper ─────────────────────────────────────────────────────────

def _last_midnight_gst() -> datetime:
    """Return the most recent midnight in GST (UTC+4) as a naive UTC datetime."""
    gst = timezone(timedelta(hours=4))
    now_gst = datetime.now(gst)
    midnight_gst = now_gst.replace(hour=0, minute=0, second=0, microsecond=0)
    # Convert back to UTC, strip tzinfo so it matches naive DB datetimes
    return midnight_gst.astimezone(timezone.utc).replace(tzinfo=None)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[Contact])
def list_contacts(
    niche: Optional[str] = Query(None),
    has_ads: Optional[bool] = Query(None),
    traffic: Optional[str] = Query(None),
    email_type: Optional[str] = Query(None),
    contacted: Optional[bool] = Query(None),
    session: Session = Depends(get_session),
):
    cutoff = _last_midnight_gst()
    q = select(Contact).where(Contact.discovered_at >= cutoff).order_by(Contact.discovered_at.desc())
    results = session.exec(q).all()

    if niche:
        results = [c for c in results if niche.lower() in c.niche.lower()]
    if has_ads is not None:
        results = [c for c in results if c.has_ads == has_ads]
    if traffic:
        results = [c for c in results if c.traffic_guess == traffic]
    if email_type:
        results = [c for c in results if c.email_type == email_type]
    if contacted is not None:
        results = [c for c in results if c.contacted == contacted]

    return results


@router.delete("/{contact_id}", status_code=204)
def delete_contact(contact_id: int, session: Session = Depends(get_session)):
    c = session.get(Contact, contact_id)
    if c:
        session.delete(c)
        session.commit()


@router.patch("/{contact_id}/contacted", response_model=Contact)
def toggle_contacted(contact_id: int, session: Session = Depends(get_session)):
    c = session.get(Contact, contact_id)
    if not c:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Contact not found")
    c.contacted = not c.contacted
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


@router.get("/export/csv")
def export_csv(
    niche: Optional[str] = Query(None),
    has_ads: Optional[bool] = Query(None),
    traffic: Optional[str] = Query(None),
    session: Session = Depends(get_session),
):
    cutoff = _last_midnight_gst()
    results = session.exec(
        select(Contact)
        .where(Contact.discovered_at >= cutoff)
        .order_by(Contact.discovered_at.desc())
    ).all()
    if niche:
        results = [c for c in results if niche.lower() in c.niche.lower()]
    if has_ads is not None:
        results = [c for c in results if c.has_ads == has_ads]
    if traffic:
        results = [c for c in results if c.traffic_guess == traffic]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["website_url", "email", "email_type", "niche",
                     "traffic_guess", "has_ads", "discovered_at", "notes", "contacted"])
    for c in results:
        writer.writerow([
            c.website_url, c.email, c.email_type, c.niche,
            c.traffic_guess, c.has_ads,
            c.discovered_at.strftime("%Y-%m-%d %H:%M UTC") if c.discovered_at else "",
            c.notes, c.contacted,
        ])

    output.seek(0)
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=adcontact_{today_str}.csv"},
    )


@router.get("/stats")
def stats(session: Session = Depends(get_session)):
    from models.contact import ScrapeJob
    all_contacts = session.exec(select(Contact)).all()
    cutoff = _last_midnight_gst()
    today_new = [c for c in all_contacts if c.discovered_at and c.discovered_at >= cutoff]
    jobs = session.exec(select(ScrapeJob)).all()
    # Read Serper usage from shared counter file (synced with ARIA backend)
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "../../"))
        import serper_counter
        serper_used = serper_counter.read()
    except Exception:
        serper_used = 0
    return {
        "total": len(all_contacts),
        "today": len(today_new),
        "jobs_run": len(jobs),
        "serper_queries_used": serper_used,
        "serper_limit": 2500,
    }
