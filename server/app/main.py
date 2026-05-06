import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .api.agent import router as agent_router
from .auth import NotAuthenticated, login_redirect_for
from .config import settings
from .database import Base, SessionLocal, engine, get_db
from .models import IdentityProvider, Tenant, User, UserRole
from . import seed
from .web.views import router as web_router
from .web.views_login import router as login_router
from .web.views_portal import router as portal_router
from .web.views_settings import router as settings_router
from .web.views_sso import router as sso_router
from .web.views_tickets import router as tickets_router
from .web.views_users import router as users_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("octoassist")

app = FastAPI(
    title="OctoAssist ITSM",
    version="0.2.0",
    description="Phase 1 Asset Discovery + Phase 2 Ticketing + SSO",
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
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_root_templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# JSON API
app.include_router(agent_router)        # /api/v1/agent/*  — Bearer (used by OctoAssistAgent.exe)

# Browser routes
app.include_router(login_router)        # /login, /logout
app.include_router(sso_router)          # /auth/oidc/{idp_id}/{start,callback}
app.include_router(web_router)          # /assets, /asset/{id}, /enrolment
app.include_router(tickets_router)      # /tickets, /tickets/{id}, ...
app.include_router(users_router)        # /users, /users/new
app.include_router(settings_router)     # /settings, /settings/idp/*
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


@app.get("/", response_class=HTMLResponse)
def root(request: Request, db: Session = Depends(get_db)):
    """Public landing page if not authenticated; role-aware redirect if signed in."""
    user_id = request.session.get("user_id") if hasattr(request, "session") else None
    if user_id is not None:
        user = db.get(User, int(user_id))
        if user is not None and user.is_active:
            target = "/portal" if user.role == UserRole.requester else "/tickets"
            return RedirectResponse(url=target, status_code=303)
    enabled_idps = (db.query(IdentityProvider)
                      .filter(IdentityProvider.is_enabled == True)  # noqa: E712
                      .order_by(IdentityProvider.display_name).all())
    tenant = db.query(Tenant).first()
    return _root_templates.TemplateResponse(
        request=request, name="landing.html",
        context={"current_user": None, "tenant": tenant, "enabled_idps": enabled_idps},
    )


@app.post("/contact", response_class=HTMLResponse)
def contact_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    org: str = Form(""),
    phone: str = Form(""),
    message: str = Form(""),
):
    """Demo-request form on the landing page.

    No SMTP wired up yet — log the inquiry server-side so we don't lose it,
    then render a thank-you page. Phase 4 wires this to email.
    """
    log.info(
        "DEMO_REQUEST name=%r email=%r org=%r phone=%r message=%r",
        name, email, org, phone, message,
    )
    return _root_templates.TemplateResponse(
        request=request, name="contact_thanks.html",
        context={"current_user": None, "tenant": None, "name": name},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
