"""Dashboard — single call computes everything the landing page needs,
adapted to the viewer's role AND the chosen date window.

  view='week'  : Mon-Sun containing `anchor` (default = today). KPIs use
                 the user's timesheet for that period.
  view='month' : 1st-to-last-day of `anchor`'s month. KPIs aggregate
                 every entry the user has on weekdays in that month
                 (regardless of which timesheet they live on).

All queries are tenant-scoped via the same FK chain used elsewhere
(TimeEntry → Timesheet.tenant_id, Engagement.tenant_id, …) — there is no
path that could leak rows from another tenant.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..models import (
    Client, Engagement, Expense, ExpenseStatus, Milestone, MilestoneStatus,
    ProjectStatus, RagStatus, StatusUpdate, TimeEntry, Timesheet,
    TimesheetPeriod, TimesheetStatus, User, UserRole,
)
from .timesheet import (
    billable_hours, day_columns, get_or_create_period, get_or_create_timesheet,
    monday_of, total_hours,
)


ViewMode = Literal["week", "month"]


@dataclass
class DashboardData:
    # Window
    role:            str
    view:            str            # "week" | "month"
    anchor:          date           # the date the user picked
    range_start:     date           # inclusive
    range_end:       date           # inclusive
    range_label:     str            # display string
    prev_anchor:     date           # ← arrow link
    next_anchor:     date           # → arrow link

    # My hours over the window
    my_total:        float
    my_billable:     float
    my_days_logged:  int

    # Week-mode-only: the timesheet for that week
    my_sheet:        Timesheet | None  = None
    my_status:       str | None        = None

    # Month-mode-only: rollup of sheets that fall in this month
    month_sheet_summary: dict | None  = None  # {approved, submitted, draft, total}

    my_recent:       list = field(default_factory=list)
    active_projects: list = field(default_factory=list)

    # Expense rollup (always shown — totals over the window)
    my_expenses_draft:       int = 0
    my_expenses_submitted:   int = 0
    my_expenses_awaiting:    float = 0.0
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

    # Practice pulse — tenant-wide rollups (admin + manager see these)
    practice: dict | None = None


def _resolve_window(anchor: date, view: ViewMode) -> tuple[date, date, str, date, date]:
    """Return (start, end, label, prev_anchor, next_anchor) for the chosen
    view. `prev_anchor` / `next_anchor` are anchors that the arrow links
    can hand straight to compute() for the previous / next bucket."""
    if view == "month":
        start = anchor.replace(day=1)
        _, last_day = calendar.monthrange(start.year, start.month)
        end = start.replace(day=last_day)
        label = start.strftime("%B %Y")
        # Prev / next anchors = first day of adjacent month
        prev_a = (start - timedelta(days=1)).replace(day=1)
        # next-month first day
        if start.month == 12:
            next_a = date(start.year + 1, 1, 1)
        else:
            next_a = date(start.year, start.month + 1, 1)
        return start, end, label, prev_a, next_a

    # default: week
    mon = monday_of(anchor)
    sun = mon + timedelta(days=6)
    label = f"Week of {mon.strftime('%d %b')} → {sun.strftime('%d %b %Y')}"
    return mon, sun, label, mon - timedelta(days=7), mon + timedelta(days=7)


def compute(db: Session, user: User,
            anchor: date | None = None,
            view: ViewMode = "week") -> DashboardData:
    today = date.today()
    if anchor is None:
        anchor = today
    if view not in ("week", "month"):
        view = "week"

    range_start, range_end, range_label, prev_a, next_a = _resolve_window(anchor, view)

    # ─── Hours over the window ──────────────────────────────────────
    if view == "week":
        period = get_or_create_period(db, user.tenant_id, anchor)
        sheet  = get_or_create_timesheet(db, user, period)
        my_total_v    = total_hours(sheet)
        my_billable_v = billable_hours(sheet)
        my_days_v     = len({e.entry_date for e in sheet.entries if (e.hours or 0) > 0})
        my_status_v   = sheet.status.value
        my_sheet_v    = sheet
        month_summary = None
    else:
        # Aggregate across every entry of mine in the month
        entries = (db.query(TimeEntry)
                     .join(Timesheet, Timesheet.id == TimeEntry.timesheet_id)
                     .filter(Timesheet.user_id == user.id,
                             TimeEntry.entry_date >= range_start,
                             TimeEntry.entry_date <= range_end).all())
        my_total_v    = round(sum(float(e.hours or 0) for e in entries), 2)
        my_billable_v = round(sum(float(e.hours or 0) for e in entries if e.is_billable), 2)
        my_days_v     = len({e.entry_date for e in entries if (e.hours or 0) > 0})
        my_status_v   = None
        my_sheet_v    = None
        # Sheet status rollup for the month: count sheets whose week overlaps the month
        month_sheets = (db.query(Timesheet)
                          .join(TimesheetPeriod, TimesheetPeriod.id == Timesheet.period_id)
                          .filter(Timesheet.user_id == user.id,
                                  TimesheetPeriod.start_date <= range_end,
                                  TimesheetPeriod.end_date   >= range_start).all())
        counts = {"draft": 0, "submitted": 0, "approved": 0, "rejected": 0}
        for s in month_sheets:
            counts[s.status.value] = counts.get(s.status.value, 0) + 1
        month_summary = {
            "total":     len(month_sheets),
            "approved":  counts["approved"],
            "submitted": counts["submitted"],
            "draft":     counts["draft"],
            "rejected":  counts["rejected"],
        }

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

    # ─── Expense rollup (totals over the window) ───
    my_exp = (db.query(Expense)
                .filter(Expense.user_id == user.id,
                        Expense.expense_date >= range_start,
                        Expense.expense_date <= range_end).all())
    draft_n = sum(1 for e in my_exp if e.status in (ExpenseStatus.draft, ExpenseStatus.rejected))
    sub_n   = sum(1 for e in my_exp if e.status == ExpenseStatus.submitted)
    awaiting_val = sum(float(e.amount or 0) for e in my_exp if e.status == ExpenseStatus.approved)
    currency = next((e.currency for e in my_exp
                     if e.status == ExpenseStatus.approved), None) or "INR"

    data = DashboardData(
        role            = user.role.value,
        view            = view,
        anchor          = anchor,
        range_start     = range_start,
        range_end       = range_end,
        range_label     = range_label,
        prev_anchor     = prev_a,
        next_anchor     = next_a,
        my_total        = my_total_v,
        my_billable     = my_billable_v,
        my_days_logged  = my_days_v,
        my_sheet        = my_sheet_v,
        my_status       = my_status_v,
        month_sheet_summary   = month_summary,
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

        # Team compliance is always anchored to the CURRENT week — that's
        # what managers actually care about ("did the team submit?").
        cur_period = get_or_create_period(db, user.tenant_id, today)
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
                                       Timesheet.period_id == cur_period.id).all()
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
        data.team_compliance_period = cur_period

        from .expenses import pending_for_approver as exp_pending
        pending_exp = exp_pending(db, user)
        data.expense_pending_count = len(pending_exp)
        data.expense_pending_top   = pending_exp[:5]

        # ─── Practice pulse — tenant-wide rollups over the chosen window ──
        # Active counts
        active_users    = (db.query(func.count(User.id))
                             .filter(User.tenant_id == user.tenant_id,
                                     User.is_active.is_(True),
                                     User.role != UserRole.admin).scalar() or 0)
        active_clients  = (db.query(func.count(Client.id))
                             .filter(Client.tenant_id == user.tenant_id,
                                     Client.is_active.is_(True)).scalar() or 0)
        active_projects = (db.query(func.count(Engagement.id))
                             .filter(Engagement.tenant_id == user.tenant_id,
                                     Engagement.status == ProjectStatus.active).scalar() or 0)

        # Tenant hours (window) — sum across all consultants in the tenant
        hour_rows = (db.query(
                        func.coalesce(func.sum(TimeEntry.hours), 0).label("total"),
                        func.coalesce(func.sum(case((TimeEntry.is_billable.is_(True),
                                                    TimeEntry.hours), else_=0)), 0).label("billable"),
                     )
                     .join(Timesheet, Timesheet.id == TimeEntry.timesheet_id)
                     .filter(Timesheet.tenant_id == user.tenant_id,
                             TimeEntry.entry_date >= range_start,
                             TimeEntry.entry_date <= range_end).first())
        tenant_total    = float(hour_rows.total or 0)
        tenant_billable = float(hour_rows.billable or 0)
        tenant_bill_pct = round(100 * tenant_billable / tenant_total, 1) if tenant_total else 0

        # Tenant-wide expenses in window: count + value (any status)
        exp_window_rows = (db.query(Expense.status, func.count(Expense.id),
                                    func.coalesce(func.sum(Expense.amount), 0))
                             .filter(Expense.tenant_id == user.tenant_id,
                                     Expense.expense_date >= range_start,
                                     Expense.expense_date <= range_end)
                             .group_by(Expense.status).all())
        exp_total_n = sum(r[1] for r in exp_window_rows)
        exp_total_v = sum(float(r[2]) for r in exp_window_rows)
        exp_reimb_pending_v = sum(float(r[2]) for r in exp_window_rows
                                   if r[0] == ExpenseStatus.approved)

        # Projects on track from latest RAG per active engagement
        green = amber = red = none_n = 0
        for e in (db.query(Engagement)
                    .filter(Engagement.tenant_id == user.tenant_id,
                            Engagement.status == ProjectStatus.active).all()):
            latest = (db.query(StatusUpdate)
                        .filter(StatusUpdate.engagement_id == e.id)
                        .order_by(StatusUpdate.period_start.desc(),
                                  StatusUpdate.created_at.desc()).first())
            tag = latest.rag.value if latest else "none"
            if   tag == "green": green += 1
            elif tag == "amber": amber += 1
            elif tag == "red":   red   += 1
            else:                none_n += 1

        data.practice = {
            "active_users":    active_users,
            "active_clients":  active_clients,
            "active_projects": active_projects,
            "tenant_total":    round(tenant_total, 1),
            "tenant_billable": round(tenant_billable, 1),
            "tenant_bill_pct": tenant_bill_pct,
            "exp_count":       exp_total_n,
            "exp_value":       round(exp_total_v, 0),
            "exp_reimb_pending": round(exp_reimb_pending_v, 0),
            "rag_green":       green,
            "rag_amber":       amber,
            "rag_red":         red,
            "rag_none":        none_n,
            "rag_on_track_pct": round(100 * green / (green + amber + red), 0) if (green + amber + red) else 0,
        }

    # ─── Admin: tenant-wide utilisation + projects RAG ───
    if user.role == UserRole.admin:
        from .reports import utilisation
        from ..models import Tenant
        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
        daily = float(tenant.daily_hours) if tenant else 8.0
        # Use the chosen window as the utilisation period (defaults to current week if view=week)
        util_rows = utilisation(db, user.tenant_id, 30, daily,
                                start=range_start, end=range_end)
        if util_rows:
            avg_pct = sum(r["utilisation_pct"] for r in util_rows) / len(util_rows)
            data.tenant_utilisation = {
                "consultant_count": len(util_rows),
                "avg_pct":          round(avg_pct, 1),
                "available":        util_rows[0]["available_hours"],
                "top":              util_rows[:3],
                "bottom":           [r for r in util_rows[::-1]][:3],
            }

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
