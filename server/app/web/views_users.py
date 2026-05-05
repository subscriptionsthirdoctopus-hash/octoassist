"""Admin: list and create users (agents and requesters)."""
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import require_admin
from ..database import get_db
from ..models import Tenant, User, UserRole
from ..security import hash_password

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["users"])


@router.get("/users", response_class=HTMLResponse)
def list_users(
    request: Request,
    user: User = Depends(require_admin),
    created_email: str | None = None,
    created_password: str | None = None,
    db: Session = Depends(get_db),
):
    rows = (db.query(User)
              .filter(User.tenant_id == user.tenant_id)
              .order_by(User.role, User.email).all())
    tenant = db.query(Tenant).first()
    return templates.TemplateResponse(
        request=request,
        name="user_list.html",
        context={
            "current_user": user, "tenant": tenant, "rows": rows,
            "roles": [r.value for r in UserRole],
            "created_email": created_email,
            "created_password": created_password,
        },
    )


@router.post("/users/new")
def create_user(
    email: str = Form(...),
    full_name: str = Form(""),
    role: str = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    email_norm = email.strip().lower()
    try:
        role_val = UserRole(role)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid role")
    if db.query(User).filter(User.email == email_norm).first():
        raise HTTPException(status_code=400, detail="Email already exists")

    # Generate a one-time temporary password. Admin shows it to the user;
    # ideally the user changes it on first login (deferred to phase 3).
    tmp_password = secrets.token_urlsafe(9)
    new_user = User(
        tenant_id=user.tenant_id,
        email=email_norm,
        password_hash=hash_password(tmp_password),
        full_name=full_name.strip(),
        role=role_val,
        is_active=True,
    )
    db.add(new_user)
    db.commit()
    return RedirectResponse(
        url=f"/users?created_email={email_norm}&created_password={tmp_password}",
        status_code=303,
    )


@router.post("/users/{user_id}/deactivate")
def deactivate_user(
    user_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None or target.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    target.is_active = False
    db.commit()
    return RedirectResponse(url="/users", status_code=303)


@router.post("/users/{user_id}/activate")
def activate_user(
    user_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None or target.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    target.is_active = True
    db.commit()
    return RedirectResponse(url="/users", status_code=303)
