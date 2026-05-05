"""Login / logout — replaces HTTP Basic for the browser flow."""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import IdentityProvider, User, UserRole
from ..security import verify_password

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

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
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    # Older bootstrap rows used the literal admin_username (e.g., "admin") as the
    # email; allow that case-sensitive too.
    if user is None:
        user = db.query(User).filter(User.email == email.strip()).first()

    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        enabled_idps = (db.query(IdentityProvider)
                          .filter(IdentityProvider.is_enabled == True)  # noqa: E712
                          .order_by(IdentityProvider.display_name).all())
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"next": next or "", "error": "Invalid email or password.",
                     "tenant": None, "enabled_idps": enabled_idps},
            status_code=401,
        )

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
