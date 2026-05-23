"""Change Management views — staff only.

Workflow:
  draft → under_review → approved → in_progress → completed | failed
                       → rejected
                       → cancelled (any non-terminal state)
"""
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from ..jinja_filters import install_on
from sqlalchemy.orm import Session

from ..auth import require_staff
from ..database import get_db
from ..models import (
    Change, ChangeEventKind, ChangeRisk, ChangeStatus, ChangeType, Tenant, User, UserRole,
)
from ..services import change as change_svc

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(templates)

router = APIRouter(tags=["changes"])


def _ctx(user: User, db: Session, **extra) -> dict:
    return {"current_user": user, "tenant": db.query(Tenant).first(),
            "format_change_event": _format_change_event, **extra}


def _format_change_event(e) -> str:
    """Render a ChangeEvent payload as a human sentence — same pattern as
    _format_event in views_tickets. Keeps audit log readable."""
    k = e.kind
    a = e.after_value or {}
    b = e.before_value or {}
    if k == ChangeEventKind.created:
        bits = []
        if a.get("type"): bits.append(f"<b>{a['type']}</b>")
        if a.get("risk"): bits.append(f"<b>{a['risk']}</b> risk")
        suffix = f" ({', '.join(bits)})" if bits else ""
        return f"Created{suffix}"
    if k == ChangeEventKind.submitted_for_review:
        return "Submitted for CAB review"
    if k == ChangeEventKind.approved:
        return "✓ Approved by CAB"
    if k == ChangeEventKind.rejected:
        return "✕ Rejected by CAB"
    if k == ChangeEventKind.started:
        return "▶ Implementation started"
    if k == ChangeEventKind.completed:
        return "✓ Completed"
    if k == ChangeEventKind.failed:
        return "⚠ Failed"
    if k == ChangeEventKind.cancelled:
        return "Cancelled"
    if k == ChangeEventKind.field_changed:
        # Show which fields shifted
        changed = []
        for key in (a.keys() if a else b.keys()):
            before = b.get(key); after = a.get(key)
            if before == after:
                continue
            label = key.replace("_", " ")
            changed.append(f"<b>{label}</b>")
        if changed:
            return f"Edited {', '.join(changed)}"
        return "Edited"
    return k.value.replace("_", " ")


def _parse_dt(s: str) -> datetime | None:
    """Parse a `<input type="datetime-local">` value submitted by the browser.

    HTML datetime-local sends "YYYY-MM-DDTHH:MM" with no timezone. We treat
    it as IST wall-clock (what the user typed) and convert to UTC for storage.
    """
    from ..jinja_filters import parse_ist_input
    return parse_ist_input(s)


@router.get("/changes", response_class=HTMLResponse)
def list_changes(
    request: Request,
    status: str | None = None,
    q: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    from sqlalchemy import func as _f, or_
    qy = db.query(Change).filter(Change.tenant_id == user.tenant_id)
    needle = (q or "").strip()
    if needle:
        like = f"%{needle.lower()}%"
        qy = qy.filter(or_(
            _f.lower(Change.title).like(like),
            _f.lower(Change.description).like(like),
            _f.lower(Change.change_number).like(like),
        ))
    if status:
        try:
            qy = qy.filter(Change.status == ChangeStatus(status))
        except ValueError:
            pass
    rows = qy.order_by(Change.created_at.desc()).limit(200).all()

    # "Awaiting your approval" — under_review changes the current user can act
    # on (i.e. they are NOT the requester; you can't approve your own change).
    awaiting = (db.query(Change)
                  .filter(Change.tenant_id == user.tenant_id,
                          Change.status == ChangeStatus.under_review,
                          Change.requester_id != user.id)
                  .order_by(Change.created_at.asc()).all())

    status_counts = dict(
        db.query(Change.status, _f.count(Change.id))
          .filter(Change.tenant_id == user.tenant_id)
          .group_by(Change.status).all()
    )
    kpi = {s.value: int(status_counts.get(s, 0)) for s in ChangeStatus}
    kpi["total"] = sum(kpi.values())

    return templates.TemplateResponse(
        request=request, name="changes_list.html",
        context=_ctx(user, db, rows=rows, q=needle, kpi=kpi,
                     awaiting=awaiting,
                     statuses=[s.value for s in ChangeStatus],
                     filter_status=status or ""),
    )


@router.get("/changes/new", response_class=HTMLResponse)
def new_change_form(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request=request, name="change_new.html",
        context=_ctx(user, db,
                     types=[t.value for t in ChangeType],
                     risks=[r.value for r in ChangeRisk]),
    )


@router.post("/changes/new")
def new_change_submit(
    title: str = Form(...),
    description: str = Form(""),
    change_type: str = Form("normal"),
    risk: str = Form("medium"),
    impact: str = Form(""),
    rollback_plan: str = Form(""),
    planned_start: str = Form(""),
    planned_end: str = Form(""),
    submit_now: int = Form(0),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    try:
        ctype = ChangeType(change_type)
    except ValueError:
        ctype = ChangeType.normal
    try:
        crisk = ChangeRisk(risk)
    except ValueError:
        crisk = ChangeRisk.medium
    c = change_svc.create_change(
        db, tenant_id=user.tenant_id, requester=user,
        title=title, description=description,
        change_type=ctype, risk=crisk,
        rollback_plan=rollback_plan, impact=impact,
        planned_start=_parse_dt(planned_start),
        planned_end=_parse_dt(planned_end),
    )
    # Optional: auto-route to CAB approval queue. Standard / emergency
    # change types skip CAB by design (standard = pre-approved, emergency =
    # post-implementation review), so only auto-submit normal changes.
    if submit_now and ctype == ChangeType.normal:
        change_svc.submit_for_review(db, change=c, actor=user)
    return RedirectResponse(url=f"/changes/{c.id}", status_code=303)


@router.get("/changes/{change_id}", response_class=HTMLResponse)
def change_detail(
    change_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    c = db.get(Change, change_id)
    if c is None or c.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    staff = (db.query(User)
               .filter(User.tenant_id == user.tenant_id, User.is_active == True,  # noqa: E712
                       User.role.in_([UserRole.admin, UserRole.agent]))
               .order_by(User.full_name, User.email).all())
    return templates.TemplateResponse(
        request=request, name="change_detail.html",
        context=_ctx(user, db, change=c, staff=staff,
                     types=[t.value for t in ChangeType],
                     risks=[r.value for r in ChangeRisk]),
    )


@router.post("/changes/{change_id}/edit")
def change_edit(
    change_id: int,
    title: str = Form(...),
    description: str = Form(""),
    change_type: str = Form("normal"),
    risk: str = Form("medium"),
    impact: str = Form(""),
    rollback_plan: str = Form(""),
    planned_start: str = Form(""),
    planned_end: str = Form(""),
    implementer_id: str = Form(""),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    c = db.get(Change, change_id)
    if c is None or c.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    if c.status not in (ChangeStatus.draft, ChangeStatus.under_review):
        raise HTTPException(status_code=400, detail="Cannot edit after CAB decision")
    c.title = title.strip()[:255]
    c.description = description.strip()
    try:
        c.change_type = ChangeType(change_type)
    except ValueError:
        pass
    try:
        c.risk = ChangeRisk(risk)
    except ValueError:
        pass
    c.impact = impact.strip()
    c.rollback_plan = rollback_plan.strip()
    c.planned_start = _parse_dt(planned_start)
    c.planned_end = _parse_dt(planned_end)
    c.implementer_id = int(implementer_id) if implementer_id.strip() else None
    db.commit()
    return RedirectResponse(url=f"/changes/{change_id}", status_code=303)


# --- Workflow transitions (each one writes to ChangeEvent log) -------------

@router.post("/changes/{change_id}/submit")
def change_submit(change_id: int, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    c = db.get(Change, change_id)
    if c is None or c.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    change_svc.submit_for_review(db, change=c, actor=user)
    return RedirectResponse(url=f"/changes/{change_id}", status_code=303)


@router.post("/changes/{change_id}/approve")
def change_approve(
    change_id: int,
    note: str = Form(""),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    c = db.get(Change, change_id)
    if c is None or c.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    if c.status != ChangeStatus.under_review:
        raise HTTPException(status_code=400, detail="Change is not under review")
    if c.requester_id == user.id:
        raise HTTPException(status_code=400, detail="Requesters cannot approve their own change")
    change_svc.approve(db, change=c, actor=user, note=note)
    return RedirectResponse(url=f"/changes/{change_id}", status_code=303)


@router.post("/changes/{change_id}/reject")
def change_reject(
    change_id: int,
    note: str = Form(""),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    c = db.get(Change, change_id)
    if c is None or c.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    if c.status != ChangeStatus.under_review:
        raise HTTPException(status_code=400, detail="Change is not under review")
    change_svc.reject(db, change=c, actor=user, note=note)
    return RedirectResponse(url=f"/changes/{change_id}", status_code=303)


@router.post("/changes/{change_id}/start")
def change_start(change_id: int, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    c = db.get(Change, change_id)
    if c is None or c.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    change_svc.start_implementation(db, change=c, actor=user)
    return RedirectResponse(url=f"/changes/{change_id}", status_code=303)


@router.post("/changes/{change_id}/complete")
def change_complete(
    change_id: int, note: str = Form(""),
    user: User = Depends(require_staff), db: Session = Depends(get_db),
):
    c = db.get(Change, change_id)
    if c is None or c.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    change_svc.complete(db, change=c, actor=user, note=note)
    return RedirectResponse(url=f"/changes/{change_id}", status_code=303)


@router.post("/changes/{change_id}/fail")
def change_fail(
    change_id: int, note: str = Form(""),
    user: User = Depends(require_staff), db: Session = Depends(get_db),
):
    c = db.get(Change, change_id)
    if c is None or c.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    change_svc.fail(db, change=c, actor=user, note=note)
    return RedirectResponse(url=f"/changes/{change_id}", status_code=303)


@router.post("/changes/{change_id}/cancel")
def change_cancel(
    change_id: int, note: str = Form(""),
    user: User = Depends(require_staff), db: Session = Depends(get_db),
):
    c = db.get(Change, change_id)
    if c is None or c.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    change_svc.cancel(db, change=c, actor=user, note=note)
    return RedirectResponse(url=f"/changes/{change_id}", status_code=303)
