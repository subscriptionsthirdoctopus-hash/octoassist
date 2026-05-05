import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .api.agent import router as agent_router
from .auth import NotAuthenticated, login_redirect_for
from .config import settings
from .database import Base, SessionLocal, engine
from . import seed
from .web.views import router as web_router
from .web.views_login import router as login_router
from .web.views_portal import router as portal_router
from .web.views_tickets import router as tickets_router
from .web.views_users import router as users_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("octoassist")

app = FastAPI(
    title="OctoAssist ITSM",
    version="0.2.0",
    description="Phase 1 Asset Discovery + Phase 2 Ticketing",
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key,
    max_age=settings.session_max_age_seconds,
    same_site="lax",
    https_only=settings.cookie_secure,
    session_cookie="octoassist_session",
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# JSON API
app.include_router(agent_router)        # /api/v1/agent/*  — Bearer auth (used by OctoAssistAgent.exe)

# Browser routes
app.include_router(login_router)        # /login, /logout
app.include_router(web_router)          # /, /assets, /asset/{id}, /enrolment
app.include_router(tickets_router)      # /tickets, /tickets/{id}, ...
app.include_router(users_router)        # /users, /users/new
app.include_router(portal_router)       # /portal, /portal/...


@app.exception_handler(NotAuthenticated)
async def _redirect_to_login(request: Request, exc: NotAuthenticated):
    return login_redirect_for(request)


@app.on_event("startup")
def _startup() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed.run(db)
    finally:
        db.close()


@app.get("/")
def root_redirect(request: Request):
    user_id = request.session.get("user_id") if hasattr(request, "session") else None
    if user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    # Logged in — let the role-aware /tickets or /portal handle it.
    return RedirectResponse(url="/tickets", status_code=303)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
