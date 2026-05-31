"""Weekly timesheet grid (TS-101) — the primary Phase 1 surface."""
from datetime import date, timedelta
from pathlib import Path

from dateutil.parser import isoparse
from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import require_user
from ..database import get_db
from ..models import (
    Activity, Engagement, EngagementTask, TimeEntry, TimesheetStatus, User,
)
from ..services import timesheet as ts_svc

router = APIRouter(tags=["timesheet"])

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def _parse_date(s: str | None) -> date:
    if not s:
        return date.today()
    try:
        return isoparse(s).date()
    except Exception:
        return date.today()


# Root / is now owned by views_dashboard (redirects to /dashboard).


@router.get("/timesheet", response_class=HTMLResponse)
def grid(
    request: Request,
    week: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    anchor = _parse_date(week)
    period = ts_svc.get_or_create_period(db, user.tenant_id, anchor)
    sheet  = ts_svc.get_or_create_timesheet(db, user, period)

    days        = ts_svc.day_columns(period)
    grid        = ts_svc.entries_grid(sheet)
    engagements = ts_svc.assigned_engagements(db, user)
    activities  = (db.query(Activity)
                     .filter(Activity.tenant_id == user.tenant_id,
                             Activity.is_active.is_(True))
                     .order_by(Activity.name).all())

    # All tasks for engagements the user can pick — kept simple for now
    all_tasks_by_eng: dict[int, list[EngagementTask]] = {}
    for e in engagements:
        all_tasks_by_eng[e.id] = [t for t in e.tasks if t.is_active]

    prev_week = (period.start_date - timedelta(days=7)).isoformat()
    next_week = (period.start_date + timedelta(days=7)).isoformat()

    return templates.TemplateResponse(
        request=request, name="timesheet_grid.html",
        context={
            "current_user": user,
            "sheet": sheet, "period": period, "days": days,
            "grid": grid, "engagements": engagements, "activities": activities,
            "tasks_by_eng": all_tasks_by_eng,
            "totals_by_day": ts_svc.totals_by_day(sheet),
            "total_hours":   ts_svc.total_hours(sheet),
            "billable_hours":ts_svc.billable_hours(sheet),
            "prev_week": prev_week, "next_week": next_week,
            "is_editable": sheet.status in (TimesheetStatus.draft, TimesheetStatus.rejected),
            "TimesheetStatus": TimesheetStatus,
        },
    )


@router.post("/timesheet/add-row")
def add_row(
    request: Request,
    week: str = Form(...),
    engagement_id: int = Form(...),
    task_id: str = Form(""),
    activity_id: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Add a single empty row (engagement[/task/activity]) so the user can
    start entering hours into the grid. The row is materialised as 7
    zero-hour entries (Mon→Sun) so the grid renders cells immediately."""
    anchor = _parse_date(week)
    period = ts_svc.get_or_create_period(db, user.tenant_id, anchor)
    sheet  = ts_svc.get_or_create_timesheet(db, user, period)
    if sheet.status not in (TimesheetStatus.draft, TimesheetStatus.rejected):
        raise HTTPException(status_code=400, detail="Sheet is locked")

    # Resolve optional FKs
    tid = int(task_id)     if task_id and task_id.isdigit()     else None
    aid = int(activity_id) if activity_id and activity_id.isdigit() else None

    # Validate engagement is one the user can log against (TS-117)
    eng = db.get(Engagement, engagement_id)
    if not eng or eng.tenant_id != user.tenant_id:
        raise HTTPException(status_code=400, detail="Invalid engagement")

    # Default billable from activity
    billable = True
    if aid:
        act = db.get(Activity, aid)
        if act:
            billable = act.is_billable_default

    # Materialise 7 zero rows for this engagement/task/activity combo
    for d in ts_svc.day_columns(period):
        existing = (db.query(TimeEntry)
                      .filter(TimeEntry.timesheet_id == sheet.id,
                              TimeEntry.entry_date == d,
                              TimeEntry.engagement_id == engagement_id,
                              TimeEntry.task_id == tid,
                              TimeEntry.activity_id == aid)
                      .first())
        if existing is None:
            db.add(TimeEntry(
                timesheet_id=sheet.id, entry_date=d,
                engagement_id=engagement_id, task_id=tid, activity_id=aid,
                hours=0, is_billable=billable, note="",
            ))
    db.commit()
    return RedirectResponse(url=f"/timesheet?week={period.start_date.isoformat()}", status_code=303)


@router.post("/timesheet/save")
async def save(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Save edited hours and notes. Form posts every entry as
    h_<entry_id>, n_<entry_id>, b_<entry_id>."""
    form = await request.form()
    week = form.get("week", "")
    anchor = _parse_date(week)
    period = ts_svc.get_or_create_period(db, user.tenant_id, anchor)
    sheet  = ts_svc.get_or_create_timesheet(db, user, period)
    if sheet.status not in (TimesheetStatus.draft, TimesheetStatus.rejected):
        raise HTTPException(status_code=400, detail="Sheet is locked")

    by_id = {e.id: e for e in sheet.entries}
    for key, val in form.multi_items():
        if key.startswith("h_"):
            eid = int(key[2:])
            e = by_id.get(eid)
            if not e: continue
            try:
                e.hours = float(val) if val else 0
            except ValueError:
                pass
        elif key.startswith("n_"):
            eid = int(key[2:])
            e = by_id.get(eid)
            if e:
                e.note = (val or "").strip()
    # checkbox billable flags — only POSTed when checked
    bset = set()
    for key, _ in form.multi_items():
        if key.startswith("b_"):
            try: bset.add(int(key[2:]))
            except ValueError: pass
    for e in sheet.entries:
        e.is_billable = e.id in bset

    ts_svc.log_action(db, tenant_id=user.tenant_id, actor_id=user.id,
                      action="timesheet.save", resource=f"timesheet:{sheet.id}")
    db.commit()
    return RedirectResponse(url=f"/timesheet?week={period.start_date.isoformat()}", status_code=303)


@router.post("/timesheet/submit")
def submit_action(
    request: Request,
    week: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    anchor = _parse_date(week)
    period = ts_svc.get_or_create_period(db, user.tenant_id, anchor)
    sheet  = ts_svc.get_or_create_timesheet(db, user, period)
    if sheet.status in (TimesheetStatus.draft, TimesheetStatus.rejected):
        ts_svc.submit(db, sheet, user)
    return RedirectResponse(url=f"/timesheet?week={period.start_date.isoformat()}", status_code=303)


@router.post("/timesheet/recall")
def recall_action(
    request: Request,
    week: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    anchor = _parse_date(week)
    period = ts_svc.get_or_create_period(db, user.tenant_id, anchor)
    sheet  = ts_svc.get_or_create_timesheet(db, user, period)
    ts_svc.recall(db, sheet, user)
    return RedirectResponse(url=f"/timesheet?week={period.start_date.isoformat()}", status_code=303)
