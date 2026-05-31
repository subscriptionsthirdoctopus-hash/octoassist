"""Timesheet domain helpers: locate or create the current week's sheet,
compute totals, transition states.
"""
from datetime import date, timedelta, datetime, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from ..models import (
    Timesheet, TimesheetPeriod, TimeEntry, TimesheetStatus, User, Engagement,
    Assignment, AuditLog,
)


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def get_or_create_period(db: Session, tenant_id: int, any_date: date) -> TimesheetPeriod:
    start = monday_of(any_date)
    end = start + timedelta(days=6)
    p = (db.query(TimesheetPeriod)
           .filter(TimesheetPeriod.tenant_id == tenant_id,
                   TimesheetPeriod.start_date == start)
           .first())
    if p is None:
        p = TimesheetPeriod(tenant_id=tenant_id, start_date=start, end_date=end)
        db.add(p)
        db.commit()
        db.refresh(p)
    return p


def get_or_create_timesheet(db: Session, user: User, period: TimesheetPeriod) -> Timesheet:
    sheet = (db.query(Timesheet)
               .filter(Timesheet.user_id == user.id,
                       Timesheet.period_id == period.id)
               .first())
    if sheet is None:
        sheet = Timesheet(
            tenant_id=user.tenant_id,
            user_id=user.id,
            period_id=period.id,
            status=TimesheetStatus.draft,
        )
        db.add(sheet)
        db.commit()
        db.refresh(sheet)
    return sheet


def assigned_engagements(db: Session, user: User) -> list[Engagement]:
    """Engagements the user may log against (TS-117). Admins see all
    engagements; others see only assigned ones."""
    from ..models import UserRole
    q = db.query(Engagement).filter(Engagement.tenant_id == user.tenant_id)
    if user.role != UserRole.admin:
        q = q.join(Assignment, Assignment.engagement_id == Engagement.id) \
             .filter(Assignment.user_id == user.id)
    return q.order_by(Engagement.name).all()


def day_columns(period: TimesheetPeriod) -> list[date]:
    return [period.start_date + timedelta(days=i) for i in range(7)]


def entries_grid(sheet: Timesheet) -> dict[tuple[int, int | None, int | None], dict[date, TimeEntry]]:
    """Index entries by (engagement_id, task_id, activity_id) → {date: entry}
    so the template can render one row per project/task/activity combo."""
    grid: dict[tuple[int, int | None, int | None], dict[date, TimeEntry]] = {}
    for e in sheet.entries:
        key = (e.engagement_id, e.task_id, e.activity_id)
        grid.setdefault(key, {})[e.entry_date] = e
    return grid


def totals_by_day(sheet: Timesheet) -> dict[date, float]:
    out: dict[date, float] = {}
    for e in sheet.entries:
        out[e.entry_date] = float(out.get(e.entry_date, 0)) + float(e.hours or 0)
    return out


def total_hours(sheet: Timesheet) -> float:
    return sum(float(e.hours or 0) for e in sheet.entries)


def billable_hours(sheet: Timesheet) -> float:
    return sum(float(e.hours or 0) for e in sheet.entries if e.is_billable)


def log_action(db: Session, *, tenant_id: int, actor_id: int | None,
               action: str, resource: str = "", detail: str = "") -> None:
    db.add(AuditLog(tenant_id=tenant_id, actor_id=actor_id,
                    action=action, resource=resource, detail=detail))


def submit(db: Session, sheet: Timesheet, actor: User) -> None:
    sheet.status = TimesheetStatus.submitted
    sheet.submitted_at = datetime.now(timezone.utc)
    log_action(db, tenant_id=actor.tenant_id, actor_id=actor.id,
               action="timesheet.submit", resource=f"timesheet:{sheet.id}")
    db.commit()


def approve(db: Session, sheet: Timesheet, actor: User) -> None:
    sheet.status = TimesheetStatus.approved
    sheet.approved_at = datetime.now(timezone.utc)
    sheet.approver_id = actor.id
    log_action(db, tenant_id=actor.tenant_id, actor_id=actor.id,
               action="timesheet.approve", resource=f"timesheet:{sheet.id}")
    db.commit()


def reject(db: Session, sheet: Timesheet, actor: User, reason: str) -> None:
    sheet.status = TimesheetStatus.rejected
    sheet.rejection_reason = reason
    log_action(db, tenant_id=actor.tenant_id, actor_id=actor.id,
               action="timesheet.reject", resource=f"timesheet:{sheet.id}",
               detail=reason or "")
    db.commit()


def recall(db: Session, sheet: Timesheet, actor: User) -> None:
    """Pull a still-pending submission back to Draft (TS-122)."""
    if sheet.status != TimesheetStatus.submitted:
        return
    sheet.status = TimesheetStatus.draft
    sheet.submitted_at = None
    log_action(db, tenant_id=actor.tenant_id, actor_id=actor.id,
               action="timesheet.recall", resource=f"timesheet:{sheet.id}")
    db.commit()


# ─────────────────────────────── Approval permissions ────────────────

def can_approve(viewer, sheet) -> bool:
    """Permission gate used by both the inbox query and per-sheet POSTs.
    Admins can approve any submitted sheet in their tenant. Managers can
    approve a sheet whose owner reports to them. Nobody else can.
    """
    from ..models import UserRole
    if sheet.tenant_id != viewer.tenant_id:
        return False
    if viewer.role == UserRole.admin:
        return True
    # Manager rule: the sheet's owner reports to this user
    return sheet.user is not None and sheet.user.manager_id == viewer.id


def pending_for_approver(db, viewer):
    """All submitted timesheets the viewer may approve."""
    from ..models import UserRole, Timesheet, TimesheetStatus, User
    q = (db.query(Timesheet)
           .join(User, User.id == Timesheet.user_id)
           .filter(Timesheet.tenant_id == viewer.tenant_id,
                   Timesheet.status == TimesheetStatus.submitted))
    if viewer.role != UserRole.admin:
        q = q.filter(User.manager_id == viewer.id)
    return q.order_by(Timesheet.submitted_at.asc()).all()
