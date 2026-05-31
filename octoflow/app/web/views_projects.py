"""Project Management surface.

Two pages:
  /projects        — PMO-style dashboard: every engagement, RAG, current
                     stage, % milestones met, hours-logged.
  /projects/{eid}  — per-engagement PM detail: stages timeline,
                     milestones, weekly status updates, deliverables.
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import require_user
from ..database import get_db
from ..models import (
    Deliverable, DeliverableStatus, Engagement, EngagementStage, Milestone,
    MilestoneStatus, ProjectStatus, RagStatus, StageStatus, StatusUpdate,
    TimeEntry, Timesheet, User,
)
from ..services.timesheet import log_action, monday_of

router = APIRouter(tags=["projects"])

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


# ────────────────────────────── Helpers ───────────────────────────────

def _engagement_hours(db: Session, engagement_id: int) -> float:
    """Total approved+pending hours logged against this engagement."""
    val = (db.query(func.coalesce(func.sum(TimeEntry.hours), 0))
             .filter(TimeEntry.engagement_id == engagement_id).scalar()) or 0
    return float(val)


def _milestone_progress(eng: Engagement) -> tuple[int, int]:
    total = len(eng.__dict__.get("_milestones", [])) if False else None  # placeholder
    # Recompute fresh from the relationship
    ms = list(getattr(eng, "_loaded_milestones", []))
    return (0, 0)  # filled below by view directly


def _latest_status(db: Session, engagement_id: int) -> StatusUpdate | None:
    return (db.query(StatusUpdate)
              .filter(StatusUpdate.engagement_id == engagement_id)
              .order_by(StatusUpdate.period_start.desc(), StatusUpdate.created_at.desc())
              .first())


def _current_stage(db: Session, engagement_id: int) -> EngagementStage | None:
    """Pick the first stage that is in-progress; else the first not-started;
    else the last completed. Gives a sensible 'where are we' reading."""
    stages = (db.query(EngagementStage)
                .filter(EngagementStage.engagement_id == engagement_id)
                .order_by(EngagementStage.sort_order).all())
    in_prog = next((s for s in stages if s.status == StageStatus.in_progress), None)
    if in_prog: return in_prog
    not_started = next((s for s in stages if s.status == StageStatus.not_started), None)
    if not_started: return not_started
    completed = [s for s in stages if s.status == StageStatus.completed]
    return completed[-1] if completed else (stages[0] if stages else None)


# ────────────────────────────── /projects (dashboard) ─────────────────

@router.get("/projects", response_class=HTMLResponse)
def projects_dashboard(
    request: Request, status: str | None = None,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    q = (db.query(Engagement)
           .filter(Engagement.tenant_id == user.tenant_id))
    if status == "active":
        q = q.filter(Engagement.status == ProjectStatus.active)
    elif status == "on_hold":
        q = q.filter(Engagement.status == ProjectStatus.on_hold)
    elif status == "closed":
        q = q.filter(Engagement.status == ProjectStatus.closed)
    engagements = q.order_by(Engagement.status, Engagement.name).all()

    rows = []
    for e in engagements:
        ms = list(e.__class__.__name__ and db.query(Milestone)
                  .filter(Milestone.engagement_id == e.id).all())
        ms_total = len(ms)
        ms_met = sum(1 for m in ms if m.status == MilestoneStatus.met)
        ms_pct = round(100 * ms_met / ms_total) if ms_total else 0
        latest = _latest_status(db, e.id)
        cur = _current_stage(db, e.id)
        rows.append({
            "eng": e,
            "current_stage": cur,
            "ms_total": ms_total, "ms_met": ms_met, "ms_pct": ms_pct,
            "rag": latest.rag.value if latest else "none",
            "rag_age_days": (date.today() - latest.period_start).days if latest else None,
            "hours": _engagement_hours(db, e.id),
        })

    counts_by_status = {
        "all":     len(engagements),
        "active":  sum(1 for e in engagements if e.status == ProjectStatus.active),
        "on_hold": sum(1 for e in engagements if e.status == ProjectStatus.on_hold),
        "closed":  sum(1 for e in engagements if e.status == ProjectStatus.closed),
    }
    return templates.TemplateResponse(
        request=request, name="projects_dashboard.html",
        context={"current_user": user, "rows": rows, "active_filter": status or "all",
                 "counts": counts_by_status},
    )


# ────────────────────────────── /projects/{eid} (PM detail) ───────────

@router.get("/projects/{eid}", response_class=HTMLResponse)
def project_detail(
    request: Request, eid: int, flash: str | None = None,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    e = db.get(Engagement, eid)
    if not e or e.tenant_id != user.tenant_id:
        raise HTTPException(404)
    stages = (db.query(EngagementStage)
                .filter(EngagementStage.engagement_id == eid)
                .order_by(EngagementStage.sort_order).all())
    milestones = (db.query(Milestone)
                    .filter(Milestone.engagement_id == eid)
                    .order_by(Milestone.due_date).all())
    deliverables = (db.query(Deliverable)
                      .filter(Deliverable.engagement_id == eid)
                      .order_by(Deliverable.due_date.is_(None), Deliverable.due_date).all())
    statuses = (db.query(StatusUpdate)
                  .filter(StatusUpdate.engagement_id == eid)
                  .order_by(StatusUpdate.period_start.desc(), StatusUpdate.created_at.desc())
                  .limit(8).all())
    hours = _engagement_hours(db, eid)
    ms_met = sum(1 for m in milestones if m.status == MilestoneStatus.met)
    ms_pct = round(100 * ms_met / len(milestones)) if milestones else 0
    return templates.TemplateResponse(
        request=request, name="project_detail.html",
        context={
            "current_user": user, "engagement": e, "client": e.client,
            "stages": stages, "milestones": milestones, "deliverables": deliverables,
            "statuses": statuses, "hours": hours, "ms_pct": ms_pct, "ms_met": ms_met,
            "today": date.today(), "monday": monday_of(date.today()),
            "stage_states": list(StageStatus),
            "milestone_states": list(MilestoneStatus),
            "deliverable_states": list(DeliverableStatus),
            "rag_choices": list(RagStatus),
            "flash": flash,
        },
    )


# ───────── Stages CRUD ─────────

@router.post("/projects/{eid}/stages/new")
def stage_new(eid: int, name: str = Form(...),
              user: User = Depends(require_user), db: Session = Depends(get_db)):
    e = db.get(Engagement, eid)
    if not e or e.tenant_id != user.tenant_id:
        raise HTTPException(404)
    max_order = (db.query(func.coalesce(func.max(EngagementStage.sort_order), 0))
                   .filter(EngagementStage.engagement_id == eid).scalar()) or 0
    db.add(EngagementStage(engagement_id=eid, name=name.strip(), sort_order=max_order + 1))
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="stage.create",
               resource=f"engagement:{e.code}")
    db.commit()
    return RedirectResponse(url=f"/projects/{eid}?flash={quote('Stage added')}", status_code=303)


@router.post("/projects/{eid}/stages/{sid}/edit")
def stage_edit(eid: int, sid: int,
               name: str = Form(...), status: str = Form("not_started"),
               start_date: str = Form(""), end_date: str = Form(""),
               user: User = Depends(require_user), db: Session = Depends(get_db)):
    s = db.get(EngagementStage, sid)
    if not s or s.engagement_id != eid:
        raise HTTPException(404)
    s.name = name.strip()
    try:
        new_status = StageStatus(status)
        if new_status == StageStatus.completed and s.status != StageStatus.completed:
            s.completed_at = datetime.now(timezone.utc)
        s.status = new_status
    except ValueError:
        pass
    s.start_date = date.fromisoformat(start_date) if start_date else None
    s.end_date   = date.fromisoformat(end_date) if end_date else None
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="stage.update",
               resource=f"stage:{sid}")
    db.commit()
    return RedirectResponse(url=f"/projects/{eid}?flash={quote('Stage saved')}", status_code=303)


# ───────── Milestones CRUD ─────────

@router.post("/projects/{eid}/milestones/new")
def milestone_new(eid: int,
                  name: str = Form(...), due_date: str = Form(...), notes: str = Form(""),
                  user: User = Depends(require_user), db: Session = Depends(get_db)):
    e = db.get(Engagement, eid)
    if not e or e.tenant_id != user.tenant_id:
        raise HTTPException(404)
    try:
        d = date.fromisoformat(due_date)
    except ValueError:
        return RedirectResponse(url=f"/projects/{eid}?flash={quote('Invalid date')}", status_code=303)
    db.add(Milestone(engagement_id=eid, name=name.strip(), due_date=d,
                     status=MilestoneStatus.planned, notes=notes.strip()))
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="milestone.create",
               resource=f"engagement:{e.code}")
    db.commit()
    return RedirectResponse(url=f"/projects/{eid}?flash={quote('Milestone added')}", status_code=303)


@router.post("/projects/{eid}/milestones/{mid}/edit")
def milestone_edit(eid: int, mid: int,
                   name: str = Form(...), due_date: str = Form(...),
                   status: str = Form("planned"), notes: str = Form(""),
                   user: User = Depends(require_user), db: Session = Depends(get_db)):
    m = db.get(Milestone, mid)
    if not m or m.engagement_id != eid:
        raise HTTPException(404)
    m.name = name.strip()
    m.due_date = date.fromisoformat(due_date)
    try:
        new_status = MilestoneStatus(status)
        if new_status == MilestoneStatus.met and m.status != MilestoneStatus.met:
            m.met_at = datetime.now(timezone.utc)
        m.status = new_status
    except ValueError:
        pass
    m.notes = notes.strip()
    db.commit()
    return RedirectResponse(url=f"/projects/{eid}?flash={quote('Milestone saved')}", status_code=303)


@router.post("/projects/{eid}/milestones/{mid}/delete")
def milestone_delete(eid: int, mid: int,
                     user: User = Depends(require_user), db: Session = Depends(get_db)):
    m = db.get(Milestone, mid)
    if not m or m.engagement_id != eid:
        raise HTTPException(404)
    db.delete(m)
    db.commit()
    return RedirectResponse(url=f"/projects/{eid}?flash={quote('Milestone removed')}", status_code=303)


# ───────── Status updates ─────────

@router.post("/projects/{eid}/status/new")
def status_new(eid: int, rag: str = Form("green"),
               period_start: str = Form(""),
               summary: str = Form(""), blockers: str = Form(""), next_week: str = Form(""),
               user: User = Depends(require_user), db: Session = Depends(get_db)):
    e = db.get(Engagement, eid)
    if not e or e.tenant_id != user.tenant_id:
        raise HTTPException(404)
    try:
        ps = date.fromisoformat(period_start) if period_start else monday_of(date.today())
    except ValueError:
        ps = monday_of(date.today())
    try:
        r = RagStatus(rag)
    except ValueError:
        r = RagStatus.green
    db.add(StatusUpdate(engagement_id=eid, author_id=user.id, period_start=ps,
                        rag=r, summary=summary.strip(), blockers=blockers.strip(),
                        next_week=next_week.strip()))
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="status.create",
               resource=f"engagement:{e.code}", detail=r.value)
    db.commit()
    return RedirectResponse(url=f"/projects/{eid}?flash={quote('Status update posted')}", status_code=303)


# ───────── Deliverables CRUD ─────────

@router.post("/projects/{eid}/deliverables/new")
def deliverable_new(eid: int,
                    name: str = Form(...), description: str = Form(""), due_date: str = Form(""),
                    user: User = Depends(require_user), db: Session = Depends(get_db)):
    e = db.get(Engagement, eid)
    if not e or e.tenant_id != user.tenant_id:
        raise HTTPException(404)
    d = None
    if due_date:
        try:
            d = date.fromisoformat(due_date)
        except ValueError:
            pass
    db.add(Deliverable(engagement_id=eid, name=name.strip(),
                       description=description.strip(), due_date=d,
                       status=DeliverableStatus.planned))
    log_action(db, tenant_id=user.tenant_id, actor_id=user.id, action="deliverable.create",
               resource=f"engagement:{e.code}")
    db.commit()
    return RedirectResponse(url=f"/projects/{eid}?flash={quote('Deliverable added')}", status_code=303)


@router.post("/projects/{eid}/deliverables/{did}/edit")
def deliverable_edit(eid: int, did: int,
                     name: str = Form(...), description: str = Form(""), due_date: str = Form(""),
                     status: str = Form("planned"), notes: str = Form(""),
                     user: User = Depends(require_user), db: Session = Depends(get_db)):
    d = db.get(Deliverable, did)
    if not d or d.engagement_id != eid:
        raise HTTPException(404)
    d.name = name.strip()
    d.description = description.strip()
    d.due_date = date.fromisoformat(due_date) if due_date else None
    try:
        new_status = DeliverableStatus(status)
        now = datetime.now(timezone.utc)
        if new_status == DeliverableStatus.delivered and d.status != DeliverableStatus.delivered:
            d.delivered_at = now
        if new_status == DeliverableStatus.accepted and d.status != DeliverableStatus.accepted:
            d.accepted_at = now
            if d.delivered_at is None:
                d.delivered_at = now
        d.status = new_status
    except ValueError:
        pass
    d.notes = notes.strip()
    db.commit()
    return RedirectResponse(url=f"/projects/{eid}?flash={quote('Deliverable saved')}", status_code=303)
