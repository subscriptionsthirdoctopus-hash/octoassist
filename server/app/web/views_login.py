"""Login / logout — replaces HTTP Basic for the browser flow."""
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from ..jinja_filters import install_on
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import IdentityProvider, User, UserRole
from ..security import verify_password
from ..services import login_throttle

log = logging.getLogger("octoassist.auth")

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(templates)

router = APIRouter(tags=["auth"])


def _safe_next(target: str | None) -> str:
    if not target or not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


def _landing_for(user: User) -> str:
    if user.role == UserRole.requester:
        return "/portal"
    return "/tickets"


@router.get("/login", response_class=HTMLResponse)
def login_form(
    request: Request,
    next: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    enabled_idps = (db.query(IdentityProvider)
                      .filter(IdentityProvider.is_enabled == True)  # noqa: E712
                      .order_by(IdentityProvider.display_name).all())
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "next": next or "",
            "error": error,
            "tenant": None,
            "enabled_idps": enabled_idps,
        },
    )


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    ip = login_throttle.client_ip(request)
    email_key = email.strip().lower()

    def _rejected(message: str, status: int):
        enabled_idps = (db.query(IdentityProvider)
                          .filter(IdentityProvider.is_enabled == True)  # noqa: E712
                          .order_by(IdentityProvider.display_name).all())
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"next": next or "", "error": message,
                     "tenant": None, "enabled_idps": enabled_idps},
            status_code=status,
        )

    # Checked BEFORE the password so a locked-out caller costs no bcrypt work
    # and learns nothing about whether the account exists.
    wait = login_throttle.retry_after(ip, email_key)
    if wait:
        log.warning("login throttled ip=%s email=%s retry_after=%ss", ip, email_key, wait)
        minutes = max(1, (wait + 59) // 60)
        return _rejected(
            f"Too many sign-in attempts. Try again in {minutes} minute{'s' if minutes > 1 else ''}, "
            "or use Sign in with Microsoft Entra ID.", 429)

    user = db.query(User).filter(User.email == email_key).first()
    # Older bootstrap rows used the literal admin_username (e.g., "admin") as the
    # email; allow that case-sensitive too.
    if user is None:
        user = db.query(User).filter(User.email == email.strip()).first()

    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        login_throttle.record_failure(ip, email_key)
        log.warning("login failed ip=%s email=%s", ip, email_key)
        return _rejected("Invalid email or password.", 401)

    login_throttle.clear(ip, email_key)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    request.session["user_id"] = user.id
    target = _safe_next(next) if next else _landing_for(user)
    return RedirectResponse(url=target, status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@router.get("/logout")
def logout_get(request: Request):
    return logout(request)
