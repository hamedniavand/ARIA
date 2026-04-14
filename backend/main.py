import base64
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select, func
from starlette.middleware.base import BaseHTTPMiddleware

from core.database import init_db, engine
from core.config import SCREENSHOTS_DIR, DASHBOARD_USER, DASHBOARD_PASS
from api.applicants import router as applicants_router
from api.positions import router as positions_router
from api.applications import router as applications_router
from api.sources import router as sources_router

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/api/health":
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:]).decode()
                user, _, pwd = decoded.partition(":")
                if secrets.compare_digest(user, DASHBOARD_USER) and \
                   secrets.compare_digest(pwd,  DASHBOARD_PASS):
                    return await call_next(request)
            except Exception:
                pass
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="ARIA"'},
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="ARIA — Academic Research & Intelligence Agent", lifespan=lifespan)

app.add_middleware(BasicAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes ────────────────────────────────────────────────────────────────
app.include_router(applicants_router,   prefix="/api/applicants",   tags=["applicants"])
app.include_router(positions_router,    prefix="/api/positions",    tags=["positions"])
app.include_router(applications_router, prefix="/api/applications", tags=["applications"])
app.include_router(sources_router,      prefix="/api/sources",      tags=["sources"])


@app.get("/api/stats")
def get_stats():
    from models.application import Application, ApplicationStatus
    from models.position import Position

    with Session(engine) as s:
        total_positions = s.exec(select(func.count(Position.id))).one()

        def count_status(status: ApplicationStatus) -> int:
            return s.exec(
                select(func.count(Application.id)).where(Application.status == status)
            ).one()

        return {
            "discovered": total_positions,
            "matched":    count_status(ApplicationStatus.matched),
            "preparing":  count_status(ApplicationStatus.preparing),
            "ready":      count_status(ApplicationStatus.ready),
            "submitted":  count_status(ApplicationStatus.submitted),
            "errors":     count_status(ApplicationStatus.error),
        }


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/api/analytics")
def get_analytics():
    """Per-source and per-applicant pipeline analytics."""
    from models.application import Application, ApplicationStatus
    from models.position import Position
    from models.source import Source
    from models.applicant import Applicant

    with Session(engine) as s:
        sources   = s.exec(select(Source)).all()
        applicants = s.exec(select(Applicant)).all()
        positions  = s.exec(select(Position)).all()
        apps       = s.exec(select(Application)).all()

    pos_map = {p.id: p for p in positions}
    apps_by_src: dict[int, list] = {}
    for a in apps:
        pos = pos_map.get(a.position_id)
        if pos:
            apps_by_src.setdefault(pos.source_id, []).append(a)

    apps_by_appl: dict[int, list] = {}
    for a in apps:
        apps_by_appl.setdefault(a.applicant_id, []).append(a)

    pos_by_src: dict[int, list] = {}
    for p in positions:
        pos_by_src.setdefault(p.source_id, []).append(p)

    def _pct(num, den):
        return round(num / den * 100, 1) if den else 0.0

    by_source = []
    for src in sources:
        src_pos   = pos_by_src.get(src.id, [])
        src_apps  = apps_by_src.get(src.id, [])
        matched   = [a for a in src_apps if a.match_score >= 70]
        submitted = [a for a in src_apps if a.status == ApplicationStatus.submitted]
        errors    = [a for a in src_apps if a.status == ApplicationStatus.error]
        avg_score = round(sum(a.match_score for a in matched) / len(matched), 1) if matched else 0.0
        by_source.append({
            "source_id":       src.id,
            "label":           src.label,
            "positions":       len(src_pos),
            "matched":         len(matched),
            "submitted":       len(submitted),
            "errors":          len(errors),
            "match_rate":      _pct(len(matched),   len(src_apps)),
            "submit_rate":     _pct(len(submitted), len(matched)),
            "avg_score":       avg_score,
        })

    by_applicant = []
    for appl in applicants:
        appl_apps  = apps_by_appl.get(appl.id, [])
        matched    = [a for a in appl_apps if a.match_score >= 70]
        submitted  = [a for a in appl_apps if a.status == ApplicationStatus.submitted]
        errors     = [a for a in appl_apps if a.status == ApplicationStatus.error]
        avg_score  = round(sum(a.match_score for a in matched) / len(matched), 1) if matched else 0.0
        by_applicant.append({
            "applicant_id":    appl.id,
            "name":            appl.name,
            "field":           appl.field_of_study,
            "total_apps":      len(appl_apps),
            "matched":         len(matched),
            "submitted":       len(submitted),
            "errors":          len(errors),
            "match_rate":      _pct(len(matched),   len(appl_apps)),
            "submit_rate":     _pct(len(submitted), len(matched)),
            "avg_score":       avg_score,
        })

    # Overall funnel
    total_pos  = len(positions)
    total_match = len([a for a in apps if a.match_score >= 70])
    total_sub   = len([a for a in apps if a.status == ApplicationStatus.submitted])
    total_err   = len([a for a in apps if a.status == ApplicationStatus.error])
    total_ready = len([a for a in apps if a.status == ApplicationStatus.ready])

    return {
        "funnel": {
            "discovered": total_pos,
            "matched":    total_match,
            "ready":      total_ready,
            "submitted":  total_sub,
            "errors":     total_err,
        },
        "by_source":    by_source,
        "by_applicant": by_applicant,
    }


# ── Static files (must come after API routes) ─────────────────────────────────
app.mount("/css",         StaticFiles(directory=str(FRONTEND_DIR / "css")), name="css")
app.mount("/js",          StaticFiles(directory=str(FRONTEND_DIR / "js")),  name="js")
app.mount("/screenshots", StaticFiles(directory=SCREENSHOTS_DIR),           name="screenshots")


@app.get("/", include_in_schema=False)
@app.get("/{full_path:path}", include_in_schema=False)
def serve_spa(full_path: str = ""):
    return FileResponse(str(FRONTEND_DIR / "index.html"))
