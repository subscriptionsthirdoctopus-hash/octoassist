"""Admin Settings — tile-grid landing + 5 CRUD pages."""
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import require_admin
from ..database import get_db
from ..models import (
    Activity, Assignment, AuditLog, Client, Engagement, EngagementTask,
    ExpenseCategory, Holiday, IdentityProvider, ProjectStatus, Tenant,
    User, UserRole,
)
from ..security import hash_password
from ..services.timesheet import log_action

router = APIRouter(tags=["settings"])

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


# ─────────────────────────────── Landing ──────────────────────────────

@router.get("/settings", response_class=HTMLResponse)
def settings_home(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    tid = user.tenant_id
    counts = {
        "users":       db.query(func.count(User.id)).filter(User.tenant_id == tid, User.is_active.is_(True)).scalar() or 0,
        "clients":     db.query(func.count(Client.id)).filter(Client.tenant_id == tid, Client.is_active.is_(True)).scalar() or 0,
        "engagements": db.query(func.count(Engagement.id)).filter(Engagement.tenant_id == tid).scalar() or 0,
        "activities":  db.query(func.count(Activity.id)).filter(Activity.tenant_id == tid, Activity.is_active.is_(True)).scalar() or 0,
        "expense_categories": db.query(func.count(ExpenseCategory.id)).filter(ExpenseCategory.tenant_id == tid, ExpenseCategory.is_active.is_(True)).scalar() or 0,
        "holidays":    db.query(func.count(Holiday.id)).filter(Holiday.tenant_id == tid).scalar() or 0,
        "audit":       db.query(func.count(AuditLog.id)).filter(AuditLog.tenant_id == tid).scalar() or 0,
        "idps":        db.query(func.count(IdentityProvider.id)).filter(IdentityProvider.tenant_id == tid).scalar() or 0,
    }
    tenant = db.query(Tenant).filter(Tenant.id == tid).first()
    return templates.TemplateResponse(
        request=request, name="settings_home.html",
        context={"current_user": user, "counts": counts, "tenant": tenant},
    )


# ─────────────────────────────── Users ────────────────────────────────

@router.get("/settings/users", response_class=HTMLResponse)
def users_list(request: Request, flash: str | None = None, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = (db.query(User).filter(User.tenant_id == user.tenant_id)
              .order_by(User.is_active.desc(), User.display_name).all())
    managers = [u for u in rows if u.role in (UserRole.admin, UserRole.manager) and u.is_active]
    return templates.TemplateResponse(
        request=request, name="settings_users.html",
        context={"current_user": user, "rows": rows, "managers": managers, "roles": list(UserRole), "flash": flash},
    )


@router.post("/settings/users/new")
def users_new(
    email: str = Form(...),
    display_name: str = Form(...),
    role: str = Form("consultant"),
    practice: str = Form(""),
    manager_id: str = Form(""),
    password: str = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        return RedirectResponse(url=f"/settings/users?flash={quote('Email already exists')}", status_code=303)
    try:
        role_enum = UserRole(role)
    except ValueError:
        role_enum = UserRole.consultant
    new_user = User(
        tenant_id=user.tenant_id, email=email, display_name=display_name.strip(),
        role=role_enum, practice=practice.strip() or None,
        manager_id=int(manager_id) if manager_id.isdigit() else None,
        password_hash=hash_password(password), is_active=True,
    )
    db.add(new_user)
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="user.create",
               resource=f"user:{email}")
    db.commit()
    return RedirectResponse(url=f"/settings/users?flash={quote(f'Added {display_name}')}", status_code=303)


@router.post("/settings/users/{uid}/edit")
def users_edit(
    uid: int,
    display_name: str = Form(...),
    role: str = Form("consultant"),
    practice: str = Form(""),
    manager_id: str = Form(""),
    is_active: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, uid)
    if not target or target.tenant_id != user.tenant_id:
        raise HTTPException(404)
    target.display_name = display_name.strip()
    try:
        target.role = UserRole(role)
    except ValueError:
        pass
    target.practice = practice.strip() or None
    target.manager_id = int(manager_id) if manager_id.isdigit() else None
    target.is_active = bool(is_active)
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="user.update",
               resource=f"user:{target.email}")
    db.commit()
    return RedirectResponse(url=f"/settings/users?flash={quote(f'Updated {target.display_name}')}", status_code=303)


@router.post("/settings/users/{uid}/password")
def users_password(uid: int, password: str = Form(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = db.get(User, uid)
    if not target or target.tenant_id != user.tenant_id:
        raise HTTPException(404)
    target.password_hash = hash_password(password)
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="user.password_reset",
               resource=f"user:{target.email}")
    db.commit()
    return RedirectResponse(url=f"/settings/users?flash={quote(f'Password reset for {target.display_name}')}", status_code=303)


# ─────────────────────────────── Clients ──────────────────────────────

@router.get("/settings/clients", response_class=HTMLResponse)
def clients_list(request: Request, flash: str | None = None, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = (db.query(Client).filter(Client.tenant_id == user.tenant_id)
              .order_by(Client.is_active.desc(), Client.name).all())
    # engagement count per client
    counts = dict(db.query(Engagement.client_id, func.count(Engagement.id))
                    .filter(Engagement.tenant_id == user.tenant_id)
                    .group_by(Engagement.client_id).all())
    return templates.TemplateResponse(
        request=request, name="settings_clients.html",
        context={"current_user": user, "rows": rows, "eng_counts": counts, "flash": flash},
    )


@router.post("/settings/clients/new")
def clients_new(code: str = Form(...), name: str = Form(...),
                user: User = Depends(require_admin), db: Session = Depends(get_db)):
    code = code.strip().upper()
    if db.query(Client).filter(Client.tenant_id == user.tenant_id, Client.code == code).first():
        return RedirectResponse(url=f"/settings/clients?flash={quote(f'Code {code} already exists')}", status_code=303)
    c = Client(tenant_id=user.tenant_id, code=code, name=name.strip(), is_active=True)
    db.add(c)
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="client.create", resource=f"client:{code}")
    db.commit()
    return RedirectResponse(url=f"/settings/clients?flash={quote(f'Added {name}')}", status_code=303)


@router.post("/settings/clients/{cid}/edit")
def clients_edit(cid: int, code: str = Form(...), name: str = Form(...), is_active: str = Form(""),
                 user: User = Depends(require_admin), db: Session = Depends(get_db)):
    c = db.get(Client, cid)
    if not c or c.tenant_id != user.tenant_id:
        raise HTTPException(404)
    c.code = code.strip().upper()
    c.name = name.strip()
    c.is_active = bool(is_active)
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="client.update", resource=f"client:{c.code}")
    db.commit()
    return RedirectResponse(url=f"/settings/clients?flash={quote(f'Updated {c.name}')}", status_code=303)


# ─────────────────── Engagements (per client) + tasks + assignments ───────

@router.get("/settings/clients/{cid}", response_class=HTMLResponse)
def client_detail(request: Request, cid: int, flash: str | None = None,
                  user: User = Depends(require_admin), db: Session = Depends(get_db)):
    c = db.get(Client, cid)
    if not c or c.tenant_id != user.tenant_id:
        raise HTTPException(404)
    engagements = (db.query(Engagement).filter(Engagement.client_id == c.id)
                     .order_by(Engagement.status, Engagement.name).all())
    staff = (db.query(User).filter(User.tenant_id == user.tenant_id, User.is_active.is_(True))
               .order_by(User.display_name).all())
    return templates.TemplateResponse(
        request=request, name="settings_client_detail.html",
        context={"current_user": user, "client": c, "engagements": engagements,
                 "staff": staff, "statuses": list(ProjectStatus), "flash": flash},
    )


@router.post("/settings/clients/{cid}/engagements/new")
def engagement_new(cid: int, code: str = Form(...), name: str = Form(...),
                   description: str = Form(""), owner_id: str = Form(""),
                   start_date: str = Form(""), end_date: str = Form(""),
                   user: User = Depends(require_admin), db: Session = Depends(get_db)):
    c = db.get(Client, cid)
    if not c or c.tenant_id != user.tenant_id:
        raise HTTPException(404)
    code = code.strip().upper()
    if db.query(Engagement).filter(Engagement.client_id == c.id, Engagement.code == code).first():
        return RedirectResponse(url=f"/settings/clients/{cid}?flash={quote(f'Code {code} already exists for this client')}", status_code=303)
    e = Engagement(
        tenant_id=user.tenant_id, client_id=c.id, code=code, name=name.strip(),
        description=description.strip(), status=ProjectStatus.active,
        owner_id=int(owner_id) if owner_id.isdigit() else None,
        start_date=date.fromisoformat(start_date) if start_date else None,
        end_date=date.fromisoformat(end_date) if end_date else None,
    )
    db.add(e)
    db.flush()
    # Seed two default tasks per engagement so consultants can log immediately
    db.add(EngagementTask(engagement_id=e.id, name="Delivery", sort_order=1))
    db.add(EngagementTask(engagement_id=e.id, name="Review",   sort_order=2))
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="engagement.create",
               resource=f"engagement:{code}")
    db.commit()
    return RedirectResponse(url=f"/settings/clients/{cid}/engagements/{e.id}?flash={quote(f'Added {e.name}')}", status_code=303)


@router.get("/settings/clients/{cid}/engagements/{eid}", response_class=HTMLResponse)
def engagement_detail(request: Request, cid: int, eid: int, flash: str | None = None,
                      user: User = Depends(require_admin), db: Session = Depends(get_db)):
    e = db.get(Engagement, eid)
    if not e or e.tenant_id != user.tenant_id or e.client_id != cid:
        raise HTTPException(404)
    staff = (db.query(User).filter(User.tenant_id == user.tenant_id, User.is_active.is_(True))
               .order_by(User.display_name).all())
    assigned_ids = {a.user_id for a in e.assignments}
    return templates.TemplateResponse(
        request=request, name="settings_engagement_detail.html",
        context={"current_user": user, "engagement": e, "client": e.client,
                 "staff": staff, "assigned_ids": assigned_ids,
                 "statuses": list(ProjectStatus), "flash": flash},
    )


@router.post("/settings/clients/{cid}/engagements/{eid}/edit")
def engagement_edit(cid: int, eid: int,
                    name: str = Form(...), description: str = Form(""),
                    status: str = Form("active"), owner_id: str = Form(""),
                    start_date: str = Form(""), end_date: str = Form(""),
                    user: User = Depends(require_admin), db: Session = Depends(get_db)):
    e = db.get(Engagement, eid)
    if not e or e.tenant_id != user.tenant_id:
        raise HTTPException(404)
    e.name = name.strip()
    e.description = description.strip()
    try:
        e.status = ProjectStatus(status)
    except ValueError:
        pass
    e.owner_id = int(owner_id) if owner_id.isdigit() else None
    e.start_date = date.fromisoformat(start_date) if start_date else None
    e.end_date   = date.fromisoformat(end_date) if end_date else None
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="engagement.update",
               resource=f"engagement:{e.code}")
    db.commit()
    return RedirectResponse(url=f"/settings/clients/{cid}/engagements/{eid}?flash={quote('Saved')}", status_code=303)


@router.post("/settings/clients/{cid}/engagements/{eid}/tasks/new")
def task_new(cid: int, eid: int, name: str = Form(...),
             user: User = Depends(require_admin), db: Session = Depends(get_db)):
    e = db.get(Engagement, eid)
    if not e or e.tenant_id != user.tenant_id:
        raise HTTPException(404)
    max_order = max([t.sort_order for t in e.tasks], default=0)
    db.add(EngagementTask(engagement_id=e.id, name=name.strip(), sort_order=max_order + 1))
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="task.create",
               resource=f"engagement:{e.code}")
    db.commit()
    return RedirectResponse(url=f"/settings/clients/{cid}/engagements/{eid}?flash={quote('Task added')}", status_code=303)


@router.post("/settings/clients/{cid}/engagements/{eid}/tasks/{tid}/toggle")
def task_toggle(cid: int, eid: int, tid: int,
                user: User = Depends(require_admin), db: Session = Depends(get_db)):
    t = db.get(EngagementTask, tid)
    if not t or t.engagement_id != eid:
        raise HTTPException(404)
    t.is_active = not t.is_active
    db.commit()
    return RedirectResponse(url=f"/settings/clients/{cid}/engagements/{eid}", status_code=303)


@router.post("/settings/clients/{cid}/engagements/{eid}/assign")
async def engagement_assign(cid: int, eid: int, request: Request,
                            user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Replace the engagement's assigned-user set with whichever check-
    boxes were ticked on the form. (Field name `assigned` is repeated
    once per checked user.)"""
    e = db.get(Engagement, eid)
    if not e or e.tenant_id != user.tenant_id:
        raise HTTPException(404)
    form = await request.form()
    wanted = {int(v) for k, v in form.multi_items() if k == "assigned" and str(v).isdigit()}
    existing = {a.user_id: a for a in e.assignments}
    for uid in wanted - set(existing.keys()):
        db.add(Assignment(user_id=uid, engagement_id=e.id))
    for uid in set(existing.keys()) - wanted:
        db.delete(existing[uid])
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="engagement.assign",
               resource=f"engagement:{e.code}", detail=f"{len(wanted)} assignees")
    db.commit()
    return RedirectResponse(url=f"/settings/clients/{cid}/engagements/{eid}?flash={quote(f'{len(wanted)} consultants assigned')}", status_code=303)


# ─────────────────────────────── Activities ───────────────────────────

@router.get("/settings/activities", response_class=HTMLResponse)
def activities_list(request: Request, flash: str | None = None,
                    user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = (db.query(Activity).filter(Activity.tenant_id == user.tenant_id)
              .order_by(Activity.is_active.desc(), Activity.code).all())
    return templates.TemplateResponse(
        request=request, name="settings_activities.html",
        context={"current_user": user, "rows": rows, "flash": flash},
    )


@router.post("/settings/activities/new")
def activity_new(code: str = Form(...), name: str = Form(...),
                 is_billable_default: str = Form(""),
                 user: User = Depends(require_admin), db: Session = Depends(get_db)):
    code = code.strip().upper()
    if db.query(Activity).filter(Activity.tenant_id == user.tenant_id, Activity.code == code).first():
        return RedirectResponse(url=f"/settings/activities?flash={quote(f'Code {code} exists')}", status_code=303)
    db.add(Activity(tenant_id=user.tenant_id, code=code, name=name.strip(),
                    is_billable_default=bool(is_billable_default), is_active=True))
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="activity.create", resource=f"activity:{code}")
    db.commit()
    return RedirectResponse(url=f"/settings/activities?flash={quote('Added')}", status_code=303)


@router.post("/settings/activities/{aid}/edit")
def activity_edit(aid: int, code: str = Form(...), name: str = Form(...),
                  is_billable_default: str = Form(""), is_active: str = Form(""),
                  user: User = Depends(require_admin), db: Session = Depends(get_db)):
    a = db.get(Activity, aid)
    if not a or a.tenant_id != user.tenant_id:
        raise HTTPException(404)
    a.code = code.strip().upper()
    a.name = name.strip()
    a.is_billable_default = bool(is_billable_default)
    a.is_active = bool(is_active)
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="activity.update", resource=f"activity:{a.code}")
    db.commit()
    return RedirectResponse(url=f"/settings/activities?flash={quote('Saved')}", status_code=303)


# ─────────────────────────────── Expense categories ──────────────────

@router.get("/settings/expense-categories", response_class=HTMLResponse)
def expcat_list(request: Request, flash: str | None = None,
                user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = (db.query(ExpenseCategory)
              .filter(ExpenseCategory.tenant_id == user.tenant_id)
              .order_by(ExpenseCategory.is_active.desc(), ExpenseCategory.code).all())
    return templates.TemplateResponse(
        request=request, name="settings_expense_categories.html",
        context={"current_user": user, "rows": rows, "flash": flash},
    )


@router.post("/settings/expense-categories/new")
def expcat_new(code: str = Form(...), name: str = Form(...),
               is_billable_default: str = Form(""),
               user: User = Depends(require_admin), db: Session = Depends(get_db)):
    code = code.strip().upper()
    if db.query(ExpenseCategory).filter(ExpenseCategory.tenant_id == user.tenant_id,
                                        ExpenseCategory.code == code).first():
        return RedirectResponse(url=f"/settings/expense-categories?flash={quote(f'Code {code} exists')}", status_code=303)
    db.add(ExpenseCategory(tenant_id=user.tenant_id, code=code, name=name.strip(),
                           is_billable_default=bool(is_billable_default), is_active=True))
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id,
               action="expense_category.create", resource=f"expense_category:{code}")
    db.commit()
    return RedirectResponse(url=f"/settings/expense-categories?flash={quote('Added')}", status_code=303)


@router.post("/settings/expense-categories/{cid}/edit")
def expcat_edit(cid: int, code: str = Form(...), name: str = Form(...),
                is_billable_default: str = Form(""), is_active: str = Form(""),
                user: User = Depends(require_admin), db: Session = Depends(get_db)):
    c = db.get(ExpenseCategory, cid)
    if not c or c.tenant_id != user.tenant_id:
        raise HTTPException(404)
    c.code = code.strip().upper()
    c.name = name.strip()
    c.is_billable_default = bool(is_billable_default)
    c.is_active = bool(is_active)
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id,
               action="expense_category.update", resource=f"expense_category:{c.code}")
    db.commit()
    return RedirectResponse(url=f"/settings/expense-categories?flash={quote('Saved')}", status_code=303)


# ─────────────────────────────── Holidays ─────────────────────────────

@router.get("/settings/holidays", response_class=HTMLResponse)
def holidays_list(request: Request, flash: str | None = None,
                  user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = (db.query(Holiday).filter(Holiday.tenant_id == user.tenant_id)
              .order_by(Holiday.holiday_date).all())
    return templates.TemplateResponse(
        request=request, name="settings_holidays.html",
        context={"current_user": user, "rows": rows, "flash": flash},
    )


@router.post("/settings/holidays/new")
def holiday_new(name: str = Form(...), holiday_date: str = Form(...),
                user: User = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        d = date.fromisoformat(holiday_date)
    except ValueError:
        return RedirectResponse(url=f"/settings/holidays?flash={quote('Invalid date')}", status_code=303)
    if db.query(Holiday).filter(Holiday.tenant_id == user.tenant_id, Holiday.holiday_date == d).first():
        return RedirectResponse(url=f"/settings/holidays?flash={quote('Date already configured')}", status_code=303)
    db.add(Holiday(tenant_id=user.tenant_id, name=name.strip(), holiday_date=d))
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="holiday.create", resource=f"holiday:{d}")
    db.commit()
    return RedirectResponse(url=f"/settings/holidays?flash={quote('Added')}", status_code=303)


@router.post("/settings/holidays/{hid}/delete")
def holiday_delete(hid: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    h = db.get(Holiday, hid)
    if not h or h.tenant_id != user.tenant_id:
        raise HTTPException(404)
    detail = f"{h.holiday_date} {h.name}"
    db.delete(h)
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="holiday.delete", resource=detail)
    db.commit()
    return RedirectResponse(url=f"/settings/holidays?flash={quote('Removed')}", status_code=303)


# ─────────────────────────────── Audit log (read-only) ────────────────

@router.get("/settings/audit", response_class=HTMLResponse)
def audit_view(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = (db.query(AuditLog).filter(AuditLog.tenant_id == user.tenant_id)
              .order_by(AuditLog.created_at.desc()).limit(200).all())
    # Hydrate actor names in one pass
    actor_ids = {r.actor_id for r in rows if r.actor_id}
    actors = {u.id: u.display_name for u in db.query(User).filter(User.id.in_(actor_ids)).all()}
    return templates.TemplateResponse(
        request=request, name="settings_audit.html",
        context={"current_user": user, "rows": rows, "actors": actors},
    )


# ─────────────────────────────── Identity providers (SSO) ────────────

from ..crypto import encrypt, is_encrypted
from ..models import IdentityProvider, IdpKind


def _idp_redirect_uri(request: Request, idp_id: int) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/auth/oidc/{idp_id}/callback"


@router.get("/settings/identity", response_class=HTMLResponse)
def identity_list(request: Request, flash: str | None = None,
                  user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = (db.query(IdentityProvider)
              .filter(IdentityProvider.tenant_id == user.tenant_id)
              .order_by(IdentityProvider.is_active.desc(), IdentityProvider.name).all())
    return templates.TemplateResponse(
        request=request, name="settings_identity.html",
        context={"current_user": user, "rows": rows, "flash": flash,
                 "roles": list(UserRole), "kinds": list(IdpKind),
                 "redirect_uri_fn": lambda idp_id: _idp_redirect_uri(request, idp_id)},
    )


@router.post("/settings/identity/new")
def identity_new(
    name: str = Form(...),
    entra_tenant_id: str = Form(...),
    entra_client_id: str = Form(...),
    entra_client_secret: str = Form(...),
    allowed_email_domains: str = Form(""),
    default_role: str = Form("consultant"),
    is_active: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        role = UserRole(default_role)
    except ValueError:
        role = UserRole.consultant
    idp = IdentityProvider(
        tenant_id=user.tenant_id, name=name.strip(), kind=IdpKind.entra,
        entra_tenant_id=entra_tenant_id.strip(),
        entra_client_id=entra_client_id.strip(),
        entra_client_secret=encrypt(entra_client_secret.strip()),
        allowed_email_domains=allowed_email_domains.strip() or None,
        default_role=role, is_active=bool(is_active),
    )
    db.add(idp)
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id,
               action="idp.create", resource=f"idp:{name}")
    db.commit()
    return RedirectResponse(url=f"/settings/identity?flash={quote('Identity provider added')}", status_code=303)


@router.post("/settings/identity/{idp_id}/edit")
def identity_edit(
    idp_id: int,
    name: str = Form(...),
    entra_tenant_id: str = Form(""),
    entra_client_id: str = Form(""),
    entra_client_secret: str = Form(""),
    allowed_email_domains: str = Form(""),
    default_role: str = Form("consultant"),
    is_active: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    idp = db.get(IdentityProvider, idp_id)
    if not idp or idp.tenant_id != user.tenant_id:
        raise HTTPException(404)
    idp.name = name.strip()
    if entra_tenant_id.strip():  idp.entra_tenant_id  = entra_tenant_id.strip()
    if entra_client_id.strip():  idp.entra_client_id  = entra_client_id.strip()
    if entra_client_secret.strip():
        idp.entra_client_secret = encrypt(entra_client_secret.strip())
    idp.allowed_email_domains = allowed_email_domains.strip() or None
    try:
        idp.default_role = UserRole(default_role)
    except ValueError:
        pass
    idp.is_active = bool(is_active)
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id,
               action="idp.update", resource=f"idp:{idp.name}")
    db.commit()
    return RedirectResponse(url=f"/settings/identity?flash={quote('Saved')}", status_code=303)


@router.post("/settings/identity/{idp_id}/delete")
def identity_delete(idp_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    idp = db.get(IdentityProvider, idp_id)
    if not idp or idp.tenant_id != user.tenant_id:
        raise HTTPException(404)
    name = idp.name
    db.delete(idp)
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id,
               action="idp.delete", resource=f"idp:{name}")
    db.commit()
    return RedirectResponse(url=f"/settings/identity?flash={quote(f'Deleted {name}')}", status_code=303)


# ─────────────────────────────── Tenant branding ─────────────────────

@router.get("/settings/tenant", response_class=HTMLResponse)
def tenant_settings(request: Request, flash: str | None = None,
                    user: User = Depends(require_admin), db: Session = Depends(get_db)):
    t = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    return templates.TemplateResponse(
        request=request, name="settings_tenant.html",
        context={"current_user": user, "tenant": t, "flash": flash},
    )


@router.post("/settings/tenant/save")
def tenant_save(
    name: str = Form(...),
    logo_url: str = Form(""),
    primary_color: str = Form(""),
    accent_color: str = Form(""),
    support_email: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    t = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not t:
        raise HTTPException(404)
    t.name = name.strip()
    t.logo_url      = logo_url.strip() or None
    t.primary_color = primary_color.strip() or None
    t.accent_color  = accent_color.strip() or None
    t.support_email = support_email.strip() or None
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="tenant.update")
    db.commit()
    return RedirectResponse(url=f"/settings/tenant?flash={quote('Saved')}", status_code=303)
