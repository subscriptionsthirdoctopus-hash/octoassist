import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from .jinja_filters import install_on
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .api.agent import router as agent_router
from .api.agent_actions import router as agent_actions_router
from .api.agent_deploy import router as agent_deploy_router
from .api.uploads import router as uploads_router
from .auth import NotAuthenticated, login_redirect_for
from .config import settings
from .csrf import CsrfMiddleware, get_or_create_token  # noqa: F401  (drafted, not active)
from .database import Base, SessionLocal, engine, get_db
from .models import IdentityProvider, Tenant, User, UserRole
from . import seed
from .web.views import router as web_router
from .web.views_actions import router as actions_router
from .web.views_agent_download import router as agent_download_router
from .web.views_changes import router as changes_router
from .web.views_kb import router as kb_router
from .web.views_login import router as login_router
from .web.views_portal import router as portal_router
from .web.views_patches import router as patches_router
from .web.views_problems import router as problems_router
from .web.views_reports import router as reports_router
from .web.views_settings import router as settings_router
from .web.views_software import router as software_router
from .web.views_subscriptions import router as subscriptions_router
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

# CSRF middleware active. Forms globally inject and submit the CSRF token.
from .csrf import CsrfMiddleware
app.add_middleware(CsrfMiddleware)

# Trust the X-Forwarded-Proto / X-Forwarded-For headers set by hrms-nginx so
# request.url.scheme reports "https" when the client used HTTPS. Without this
# the installer script bakes "http://..." into the agent config even though
# the user requested it via https://octoassist.thirdoctopus.com.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# ---------------------------------------------------------------------------
# Notification middleware — populate request.state.notifications on every
# authenticated browser request so the bell in base.html can render server-
# side counts (no client polling needed). Skipped for /api/, /static/,
# /agent/files/ and other non-HTML paths where the cost would be wasted.
#
# IMPORTANT: Starlette runs middleware in REVERSE order of add_middleware()
# calls (last-added runs first on the request). This middleware reads
# request.session, so it must run AFTER SessionMiddleware on the request —
# which means it must be add_middleware()'d BEFORE SessionMiddleware below.
# ---------------------------------------------------------------------------
from starlette.middleware.base import BaseHTTPMiddleware
from .services import notifications_panel as _np

_SKIP_PREFIXES = ("/api/", "/static/", "/agent/files/", "/files/", "/health", "/favicon.ico")


class NotificationContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Default: empty dict so templates can always read request.state.notifications
        request.state.notifications = _np.empty()
        path = request.url.path or ""
        if not any(path.startswith(p) for p in _SKIP_PREFIXES):
            # Defensive: even though we ordered the middleware correctly, the
            # session might be missing for some early-error path.
            user_id = None
            try:
                user_id = request.session.get("user_id")
            except (AssertionError, AttributeError):
                user_id = None
            if user_id:
                db: Session = SessionLocal()
                try:
                    user = db.get(User, user_id)
                    if user and user.is_active:
                        request.state.notifications = _np.compute(db, user)
                except Exception:  # noqa: BLE001
                    # Never block the page over notification math
                    log.exception("notification middleware failed")
                finally:
                    db.close()
        return await call_next(request)


# Add notifications FIRST so SessionMiddleware (added below) wraps around
# it and runs first on the request path — populating request.session before
# the notification middleware tries to read it.
app.add_middleware(NotificationContextMiddleware)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key,
    max_age=settings.session_max_age_seconds,
    same_site="lax",  # already provides meaningful CSRF defence in modern browsers
    https_only=settings.cookie_secure,
    session_cookie="octoassist_session",
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


class CachedStaticFiles(StaticFiles):
    """Static files with an explicit cache policy.

    Without one, browsers fall back to heuristic caching off Last-Modified and
    revalidate the 110 KB stylesheet on every page. References that carry a
    ?v= cache-buster (styles.css, the table scripts) can be cached for a year
    and marked immutable — a change ships under a new ?v=. Anything requested
    without a version (the favicon) gets a day and then revalidates via ETag.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            versioned = b"v=" in scope.get("query_string", b"")
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable" if versioned else "public, max-age=86400"
            )
        return response


app.mount("/static", CachedStaticFiles(directory=str(STATIC_DIR)), name="static")

_root_templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(_root_templates)

# JSON API
app.include_router(agent_router)         # /api/v1/agent/*  — Bearer (used by OctoAssistAgent.exe)
app.include_router(agent_deploy_router)  # /api/v1/agent/deployments/* — Phase 6 auto-install
app.include_router(agent_actions_router) # /api/v1/agent/actions/*   — Phase 10 remote-action channel

# Browser routes
app.include_router(login_router)        # /login, /logout
app.include_router(sso_router)          # /auth/oidc/{idp_id}/{start,callback}
app.include_router(web_router)          # /assets, /asset/{id}, /enrolment
app.include_router(tickets_router)      # /tickets, /tickets/{id}, ...
app.include_router(problems_router)     # /problems, /problems/{id}, ...
app.include_router(changes_router)      # /changes, /changes/{id}, ...
app.include_router(reports_router)      # /reports, /reports/{tickets,sla,assets,changes}, /reports/export/*
app.include_router(patches_router)      # /patches, /patches/{agent_id}, /patches/export.csv
app.include_router(software_router)     # /software (SAM), /software/product/{pub}/{prod}, /software/export.csv
app.include_router(subscriptions_router)  # /subscriptions — owned licences, keys, POs, expiry
app.include_router(actions_router)      # /actions, /asset/{id}/{actions,processes,action}
app.include_router(uploads_router)      # /api/v1/uploads, /files/{uuid}.{ext}
app.include_router(agent_download_router) # /agent (download index), /agent/files/<name>
app.include_router(users_router)        # /users, /users/new
app.include_router(settings_router)     # /settings, /settings/idp/*
app.include_router(kb_router)           # /kb/*, /portal/kb/*
app.include_router(portal_router)       # /portal, /portal/...


@app.exception_handler(NotAuthenticated)
async def _redirect_to_login(request: Request, exc: NotAuthenticated):
    return login_redirect_for(request)


@app.on_event("startup")
def _startup() -> None:
    Base.metadata.create_all(bind=engine)
    from sqlalchemy import text
    db = SessionLocal()
    try:
        db.execute(text("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS notification_settings JSONB DEFAULT '{}'::jsonb"))
        db.execute(text("ALTER TABLE patch_windows ADD COLUMN IF NOT EXISTS group_id INTEGER REFERENCES asset_groups(id) ON DELETE SET NULL"))
        db.execute(text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS uninstall_pending BOOLEAN NOT NULL DEFAULT FALSE"))
        db.execute(text("ALTER TABLE entra_devices ADD COLUMN IF NOT EXISTS location VARCHAR(200)"))
        db.execute(text("ALTER TABLE entra_devices ADD COLUMN IF NOT EXISTS department VARCHAR(200)"))
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY,
                tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                action VARCHAR(120) NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                ip_address VARCHAR(50),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
            )
        """))
        db.commit()
        seed.run(db)
    finally:
        db.close()
    # Phase 9: background scheduler that auto-promotes patch windows whose
    # scheduled_for time has arrived. Daemon thread, idempotent.
    from .services.scheduler import start_in_background
    start_in_background()

    # Active SLA Escalation Engine
    from .services.sla_escalator import start_in_background as start_sla_escalator
    start_sla_escalator()


@app.get("/", response_class=HTMLResponse)
def root(request: Request, db: Session = Depends(get_db)):
    """Public landing page if not authenticated; role-aware redirect if signed in."""
    user_id = request.session.get("user_id") if hasattr(request, "session") else None
    if user_id is not None:
        user = db.get(User, int(user_id))
        if user is not None and user.is_active:
            # Staff land on the Dashboard; end-users land on their portal.
            target = "/portal" if user.role == UserRole.requester else "/reports"
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


@app.get("/robots.txt", response_class=Response)
def robots() -> Response:
    content = """User-agent: *
Allow: /
Disallow: /login
Disallow: /auth/
Disallow: /api/
Disallow: /asset/
Disallow: /tickets/
Disallow: /problems/
Disallow: /changes/
Disallow: /kb/
Disallow: /settings/
Disallow: /reports/
Disallow: /portal/

Sitemap: https://octoassist.thirdoctopus.com/sitemap.xml
"""
    return Response(content=content, media_type="text/plain")


@app.get("/sitemap.xml", response_class=Response)
def sitemap() -> Response:
    content = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://octoassist.thirdoctopus.com/</loc>
    <lastmod>2026-05-27</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://octoassist.thirdoctopus.com/login</loc>
    <lastmod>2026-05-27</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
</urlset>
"""
    return Response(content=content, media_type="application/xml")
