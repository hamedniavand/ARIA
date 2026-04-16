"""AdContact — ad outreach lead scraper, port 8001."""
import logging
import os
import secrets
import sys

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager

# Make sure imports work when run from ARIA/adcontact/
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from core.config import ADCONTACT_USER, ADCONTACT_PASS
from core.database import init_db
from api.jobs import router as jobs_router
from api.contacts import router as contacts_router

# ── HTTP Basic Auth ───────────────────────────────────────────────────────────

_security = HTTPBasic()

def _verify_auth(credentials: HTTPBasicCredentials = Depends(_security)):
    ok = (
        secrets.compare_digest(credentials.username.encode(), ADCONTACT_USER.encode())
        and secrets.compare_digest(credentials.password.encode(), ADCONTACT_PASS.encode())
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )


# ── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="AdContact",
    lifespan=lifespan,
    dependencies=[Depends(_verify_auth)],
)

app.include_router(jobs_router, prefix="/api/jobs", tags=["jobs"])
app.include_router(contacts_router, prefix="/api/contacts", tags=["contacts"])

# Serve frontend
_frontend = os.path.join(os.path.dirname(__file__), "frontend")
app.mount("/static", StaticFiles(directory=_frontend), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(_frontend, "index.html"))


@app.get("/api/health")
def health():
    return {"status": "ok"}
