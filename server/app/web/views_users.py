"""Admin: list and create users (agents and requesters)."""
import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from ..jinja_filters import install_on
from sqlalchemy.orm import Session

from ..auth import require_admin
from ..database import get_db
from ..models import IdentityProvider, IdentityProviderKind, Tenant, User, UserRole
from ..security import hash_password
from ..services import entra_directory
from ..services.sso import parse_entra_config

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(templates)

router = APIRouter(tags=["users"])


@router.get("/users", response_class=HTMLResponse)
def list_users(
    request: Request,
    user: User = Depends(require_admin),
    created_email: str | None = None,
    created_password: str | None = None,
    flash: str | None = None,
    error: str | None = None,
    q: str = "",
    role: str = "",
    status: str = "",
    department: str = "",
    location: str = "",
    db: Session = Depends(get_db),
):
    from sqlalchemy import distinct, func, or_
    base_q = db.query(User).filter(User.tenant_id == user.tenant_id)
    query = base_q
    needle = (q or "").strip()
    if needle:
        like = f"%{needle.lower()}%"
        query = query.filter(or_(
            func.lower(User.email).like(like),
            func.lower(User.full_name).like(like),
            func.lower(func.coalesce(User.department, "")).like(like),
            func.lower(func.coalesce(User.location,   "")).like(like),
            func.lower(func.coalesce(User.job_title,  "")).like(like),
        ))
    if role:
        try:
            query = query.filter(User.role == UserRole(role))
        except ValueError:
            pass
    if status == "active":
        query = query.filter(User.is_active == True)  # noqa: E712
    elif status == "disabled":
        query = query.filter(User.is_active == False)  # noqa: E712
    if department:
        query = query.filter(func.lower(func.coalesce(User.department, "")) == department.lower())
    if location:
        query = query.filter(func.lower(func.coalesce(User.location, "")) == location.lower())
    rows = query.order_by(User.role, User.email).all()
    # Distinct values for filter dropdowns — pulled from the full tenant set,
    # not the filtered subset, so the admin can always pivot.
    departments = sorted({d for (d,) in base_q.with_entities(distinct(User.department)).all() if d})
    locations   = sorted({l for (l,) in base_q.with_entities(distinct(User.location)).all() if l})
    tenant = db.query(Tenant).first()
    entra_idp = (db.query(IdentityProvider)
                   .filter(IdentityProvider.tenant_id == user.tenant_id,
                           IdentityProvider.kind == IdentityProviderKind.entra,
                           IdentityProvider.is_enabled == True)  # noqa: E712
                   .first())
    return templates.TemplateResponse(
        request=request,
        name="user_list.html",
        context={
            "current_user": user, "tenant": tenant, "rows": rows,
            "roles": [r.value for r in UserRole],
            "departments": departments, "locations": locations,
            "created_email": created_email,
            "created_password": created_password,
            "flash": flash, "error": error,
            "entra_idp": entra_idp,
            "q": needle,
            "filter_role": role, "filter_status": status,
            "filter_department": department, "filter_location": location,
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


# ---------- Bulk-select role change ----------

@router.post("/users/bulk-update")
async def bulk_update_users(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Apply one of {promote_agent, promote_admin, demote_requester,
    deactivate, activate} to every user_id checkbox in the form. The bulk
    toolbar in user_list.html POSTs `action=<verb>&user_ids=1&user_ids=2&...`.
    """
    form = await request.form()
    action  = (form.get("action") or "").strip()
    raw_ids = form.getlist("user_ids")
    try:
        ids = [int(x) for x in raw_ids if str(x).strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad user_ids")
    if not ids:
        return RedirectResponse(url="/users?error=No+users+selected", status_code=303)

    role_map = {
        "promote_agent":     UserRole.agent,
        "promote_admin":     UserRole.admin,
        "demote_requester":  UserRole.requester,
    }

    targets = (db.query(User)
                 .filter(User.id.in_(ids),
                         User.tenant_id == user.tenant_id)
                 .all())
    n = 0
    skipped = []  # list of (email, reason) for the flash message
    for t in targets:
        # Guard: don't let an admin demote / deactivate / delete themselves.
        if t.id == user.id and action in ("demote_requester", "deactivate", "delete"):
            skipped.append((t.email, "self"))
            continue
        if action in role_map:
            t.role = role_map[action]
            n += 1
        elif action == "deactivate":
            t.is_active = False
            n += 1
        elif action == "activate":
            t.is_active = True
            n += 1
        elif action == "delete":
            # FK RESTRICT on tickets.reporter_id means deleting a user who
            # ever reported a ticket would raise IntegrityError. Skip those
            # and surface the count in the flash; suggest deactivate instead.
            from ..models import Ticket
            ticket_count = (db.query(Ticket)
                              .filter((Ticket.reporter_id == t.id) |
                                      (Ticket.assignee_id == t.id))
                              .count())
            if ticket_count > 0:
                skipped.append((t.email, f"{ticket_count} ticket(s) reference them"))
                continue
            db.delete(t)
            n += 1
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {action}")
    db.commit()
    if skipped and action == "delete":
        skipped_str = "; ".join(f"{e} ({r})" for e, r in skipped[:5])
        more = f" (+{len(skipped) - 5} more)" if len(skipped) > 5 else ""
        msg = (f"Deleted {n} user(s). Skipped {len(skipped)}: {skipped_str}{more}. "
               f"Use deactivate for users with ticket history.")
        return RedirectResponse(url=f"/users?error={quote(msg)}", status_code=303)
    msg = f"Applied '{action}' to {n} user(s)"
    return RedirectResponse(url=f"/users?flash={quote(msg)}", status_code=303)


# ---------- Entra Graph sync ----------

@router.post("/users/sync-entra")
async def sync_entra_users(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Pull every active member user from the linked Entra tenant and upsert
    them into the OctoAssist users table as `requester`. Admin then bulk-
    promotes the people who should be staff via the checkbox UI.
    """
    idp = (db.query(IdentityProvider)
             .filter(IdentityProvider.tenant_id == user.tenant_id,
                     IdentityProvider.kind == IdentityProviderKind.entra,
                     IdentityProvider.is_enabled == True)  # noqa: E712
             .first())
    if idp is None:
        return RedirectResponse(
            url="/users?error=No+enabled+Entra+identity+provider.+Configure+one+in+Settings+%E2%86%92+Identity+providers.",
            status_code=303,
        )
    cfg = parse_entra_config(idp.config or {})
    report = await entra_directory.sync_users(db, tenant_id=user.tenant_id, cfg=cfg)
    if report.errors:
        # Surface the first error in the banner (typically permission-related)
        head = report.errors[0][:240]
        return RedirectResponse(
            url=f"/users?error={quote(f'Sync had errors. {report.summary()} — first: {head}')}",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/users?flash={quote(f'Synced from Entra: {report.summary()}')}",
        status_code=303,
    )
