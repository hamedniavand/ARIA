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


# ── Static files (must come after API routes) ─────────────────────────────────
app.mount("/css",         StaticFiles(directory=str(FRONTEND_DIR / "css")), name="css")
app.mount("/js",          StaticFiles(directory=str(FRONTEND_DIR / "js")),  name="js")
app.mount("/screenshots", StaticFiles(directory=SCREENSHOTS_DIR),           name="screenshots")


@app.get("/", include_in_schema=False)
@app.get("/{full_path:path}", include_in_schema=False)
def serve_spa(full_path: str = ""):
    return FileResponse(str(FRONTEND_DIR / "index.html"))
