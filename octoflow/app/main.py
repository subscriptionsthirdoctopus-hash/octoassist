"""OctoFlow — FastAPI entry point. Mirrors OctoAssist's wiring."""
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .database import engine, SessionLocal, Base
from .auth import NotAuthenticated
from . import seed, models  # noqa: F401 -- import models so create_all sees them
from .web.views_login import router as login_router
from .web.views_timesheet import router as timesheet_router
from .web.views_stub import router as stub_router
from .web.views_settings import router as settings_router
from .web.views_projects import router as projects_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("octoflow")

app = FastAPI(title="OctoFlow", version="0.1.0")

# Session middleware (signed, HttpOnly, SameSite=Lax)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="octoflow_session",
    same_site="lax",
    https_only=False,   # behind nginx/TLS in prod; flip when going public
)

# Static
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Routes
app.include_router(login_router)
app.include_router(timesheet_router)
app.include_router(projects_router)  # /projects PM dashboard + per-project detail
app.include_router(settings_router)  # /settings + sub-pages
app.include_router(stub_router)      # stubs for /approvals, /reports


@app.exception_handler(NotAuthenticated)
async def _redirect_to_login(request: Request, exc: NotAuthenticated):
    next_url = request.url.path
    return RedirectResponse(url=f"/login?next={next_url}", status_code=303)


@app.get("/health")
def health():
    return {"status": "ok", "service": "octoflow", "version": app.version}


@app.on_event("startup")
def _startup() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed.run(db)
    finally:
        db.close()
    log.info("OctoFlow startup complete · tenant=%s · base_url=%s",
             settings.tenant_name, settings.base_url)
