"""Login / logout — supports local-password AND per-tenant Entra SSO buttons."""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import IdentityProvider, Tenant, User
from ..security import verify_password

router = APIRouter(tags=["auth"])

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None, next: str = "/",
               db: Session = Depends(get_db)):
    # Every active IdP, grouped by tenant — one button per organisation.
    rows = (db.query(IdentityProvider, Tenant)
              .join(Tenant, Tenant.id == IdentityProvider.tenant_id)
              .filter(IdentityProvider.is_active.is_(True))
              .order_by(Tenant.name, IdentityProvider.name).all())
    by_tenant: dict[str, list] = {}
    for idp, tenant in rows:
        by_tenant.setdefault(tenant.name, []).append(idp)
    return templates.TemplateResponse(
        request=request, name="login.html",
        context={"error": error, "next": next, "current_user": None,
                 "idps_by_tenant": by_tenant},
    )


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next_url: str = Form("/", alias="next"),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    # With per-tenant email uniqueness we could in principle have the same
    # address in two tenants. Pick the active user matching this password.
    candidates = db.query(User).filter(User.email == email, User.is_active.is_(True)).all()
    user = None
    for u in candidates:
        if verify_password(password, u.password_hash or ""):
            user = u
            break
    if user is None:
        return RedirectResponse(url=f"/login?error=Invalid+email+or+password&next={next_url}",
                                status_code=303)
    request.session["user_id"] = user.id
    return RedirectResponse(url=next_url or "/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
