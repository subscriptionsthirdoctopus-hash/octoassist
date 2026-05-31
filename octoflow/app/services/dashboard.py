"""Dashboard — single call computes everything the landing page needs,
adapted to the viewer's role.

  Consultant : my week, recent sheets, active projects (read-only)
  Manager    : adds pending approvals + team submission compliance
  Admin      : adds tenant-wide utilisation snapshot + all projects RAG

All queries are tenant-scoped via the same FK chain used elsewhere
(TimeEntry → Timesheet.tenant_id, Engagement.tenant_id, …) — there is no
path that could leak rows from another tenant.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..models import (
    Engagement, Expense, ExpenseStatus, Milestone, MilestoneStatus,
    ProjectStatus, RagStatus, StatusUpdate, TimeEntry, Timesheet,
    TimesheetPeriod, TimesheetStatus, User, UserRole,
)
from .timesheet import (
    billable_hours, day_columns, get_or_create_period, get_or_create_timesheet,
    monday_of, total_hours,
)


@dataclass
class DashboardData:
    # Always shown
    role:            str
    week_start:      date
    week_end:        date
    my_sheet:        Timesheet
    my_status:       str
    my_total:        float
    my_billable:     float
    my_days_logged:  int        # number of days in the week that have any entry
    my_recent:       list       # last 5 sheets (this one included if exists)
    active_projects: list       # engagements I'm assigned to (capped at 6)

    # Expense rollup (always shown — values are tenant-zero-safe)
    my_expenses_draft:       int = 0
    my_expenses_submitted:   int = 0
    my_expenses_awaiting:    float = 0.0   # value of approved-but-not-yet-reimbursed
    my_expenses_currency:    str = "INR"

    # Manager / Admin additions
    pending_approvals_count: int = 0
    pending_approvals_top:   list = field(default_factory=list)
    team_compliance:         list = field(default_factory=list)
    team_compliance_period:  TimesheetPeriod | None = None
    expense_pending_count:   int = 0
    expense_pending_top:     list = field(default_factory=list)

    # Admin additions
    tenant_utilisation: dict | None = None
    projects_rag:       list = field(default_factory=list)


def compute(db: Session, user: User) -> DashboardData:
    today  = date.today()
    period = get_or_create_period(db, user.tenant_id, today)
    sheet  = get_or_create_timesheet(db, user, period)

    # ─── Always: my week ───
    days = day_columns(period)
    days_logged = len({e.entry_date for e in sheet.entries if (e.hours or 0) > 0})

    recent = (db.query(Timesheet)
                .filter(Timesheet.user_id == user.id)
                .order_by(Timesheet.period_id.desc())
                .limit(5).all())

    # Active projects I'm assigned to (or, for admins, all active)
    if user.role == UserRole.admin:
        active_projects = (db.query(Engagement)
                             .filter(Engagement.tenant_id == user.tenant_id,
                                     Engagement.status == ProjectStatus.active)
                             .order_by(Engagement.name).limit(6).all())
    else:
        active_projects = [a.engagement for a in user.assignments
                           if a.engagement.status == ProjectStatus.active][:6]

    # ─── My expense rollup ───
    my_exp = db.query(Expense).filter(Expense.user_id == user.id).all()
    draft_n = sum(1 for e in my_exp if e.status in (ExpenseStatus.draft, ExpenseStatus.rejected))
    sub_n   = sum(1 for e in my_exp if e.status == ExpenseStatus.submitted)
    awaiting_val = sum(float(e.amount or 0) for e in my_exp if e.status == ExpenseStatus.approved)
    # Pick the currency of the most-recent approved expense, else fall back
    currency = next((e.currency for e in my_exp
                     if e.status == ExpenseStatus.approved), None) or "INR"

    data = DashboardData(
        role            = user.role.value,
        week_start      = period.start_date,
        week_end        = period.end_date,
        my_sheet        = sheet,
        my_status       = sheet.status.value,
        my_total        = total_hours(sheet),
        my_billable     = billable_hours(sheet),
        my_days_logged  = days_logged,
        my_recent       = recent,
        active_projects = active_projects,
        my_expenses_draft     = draft_n,
        my_expenses_submitted = sub_n,
        my_expenses_awaiting  = awaiting_val,
        my_expenses_currency  = currency,
    )

    # ─── Manager + Admin: pending approvals + team compliance ───
    if user.role in (UserRole.admin, UserRole.manager):
        from .timesheet import pending_for_approver
        pending = pending_for_approver(db, user)
        data.pending_approvals_count = len(pending)
        data.pending_approvals_top   = pending[:5]

        # Team submission compliance for the current period
        # admins see all active non-admin users; managers see direct reports
        q = (db.query(User)
               .filter(User.tenant_id == user.tenant_id,
                       User.is_active.is_(True),
                       User.role != UserRole.admin))
        if user.role == UserRole.manager:
            q = q.filter(User.manager_id == user.id)
        team_users = q.order_by(User.display_name).all()
        sheets_by_user = {
            s.user_id: s for s in
            db.query(Timesheet).filter(Timesheet.tenant_id == user.tenant_id,
                                       Timesheet.period_id == period.id).all()
        }
        team = []
        for u in team_users:
            s = sheets_by_user.get(u.id)
            team.append({
                "user":    u,
                "status":  s.status.value if s else "not_started",
                "hours":   sum(float(e.hours or 0) for e in s.entries) if s else 0.0,
            })
        data.team_compliance        = team
        data.team_compliance_period = period

        # Pending expenses queue for this approver
        from .expenses import pending_for_approver as exp_pending
        pending_exp = exp_pending(db, user)
        data.expense_pending_count = len(pending_exp)
        data.expense_pending_top   = pending_exp[:5]

    # ─── Admin: tenant-wide utilisation snapshot + projects RAG ───
    if user.role == UserRole.admin:
        # 30-day rolling utilisation, tenant-wide
        from .reports import utilisation
        from ..models import Tenant
        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
        daily = float(tenant.daily_hours) if tenant else 8.0
        util_rows = utilisation(db, user.tenant_id, 30, daily)
        if util_rows:
            avg_pct = sum(r["utilisation_pct"] for r in util_rows) / len(util_rows)
            data.tenant_utilisation = {
                "consultant_count": len(util_rows),
                "avg_pct":          round(avg_pct, 1),
                "available":        util_rows[0]["available_hours"],
                "top":              util_rows[:3],
                "bottom":           [r for r in util_rows[::-1]][:3],
            }
        else:
            data.tenant_utilisation = None

        # Projects RAG — pull from latest status update per engagement
        engagements = (db.query(Engagement)
                         .filter(Engagement.tenant_id == user.tenant_id,
                                 Engagement.status == ProjectStatus.active)
                         .order_by(Engagement.name).all())
        rag_rows = []
        for e in engagements:
            latest = (db.query(StatusUpdate)
                        .filter(StatusUpdate.engagement_id == e.id)
                        .order_by(StatusUpdate.period_start.desc(),
                                  StatusUpdate.created_at.desc())
                        .first())
            ms_total = db.query(func.count(Milestone.id))\
                         .filter(Milestone.engagement_id == e.id).scalar() or 0
            ms_met = db.query(func.count(Milestone.id))\
                       .filter(Milestone.engagement_id == e.id,
                               Milestone.status == MilestoneStatus.met).scalar() or 0
            rag_rows.append({
                "engagement": e,
                "rag":        latest.rag.value if latest else "none",
                "rag_age_days": (today - latest.period_start).days if latest else None,
                "ms_total":   ms_total,
                "ms_met":     ms_met,
                "ms_pct":     round(100 * ms_met / ms_total) if ms_total else 0,
            })
        data.projects_rag = rag_rows[:8]

    return data
