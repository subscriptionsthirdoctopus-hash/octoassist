"""Phase 1 reports — TS-132 through TS-138.

Every aggregation here is tenant-scoped. The viewer's tenant_id is the
single filter that guarantees zero cross-tenant data leak — there is no
join path that could leak rows from another tenant because the FK chain
TimeEntry → Timesheet (tenant_id) → User (tenant_id) is enforced.

Each function returns a list of plain dicts so view code can render
both HTML (table) and CSV (streamed) without re-querying.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..models import (
    Activity, Client, Engagement, EngagementTask, TimeEntry, Timesheet,
    TimesheetStatus, TimesheetPeriod, User, UserRole, Holiday,
)


# ─── Helpers ────────────────────────────────────────────────────────

def resolve_window(days: int | None = 30,
                   start: date | None = None,
                   end: date | None = None) -> tuple[date, date]:
    """Pick a (start, end) range. Explicit start/end win over `days`;
    if only one bound is given the other is filled in sensibly."""
    today = date.today()
    if start and end:
        return start, end
    if start and not end:
        return start, today
    if end and not start:
        return end - timedelta(days=(days or 30) - 1), end
    return today - timedelta(days=(days or 30) - 1), today


def _window(days: int) -> tuple[date, date]:
    """Backwards-compatible single-arg form."""
    return resolve_window(days=days)


def _entries_q(db: Session, tenant_id: int, days: int | None = 30,
               start: date | None = None, end: date | None = None,
               client_id: int | None = None,
               engagement_id: int | None = None,
               user_id: int | None = None):
    """Base query: entries × timesheet × user, scoped to one tenant +
    window + optional client/engagement/user filter."""
    s, e = resolve_window(days, start, end)
    q = (db.query(TimeEntry)
           .join(Timesheet, Timesheet.id == TimeEntry.timesheet_id)
           .filter(Timesheet.tenant_id == tenant_id,
                   TimeEntry.entry_date >= s,
                   TimeEntry.entry_date <= e))
    if client_id or engagement_id:
        q = q.join(Engagement, Engagement.id == TimeEntry.engagement_id)
        if client_id:     q = q.filter(Engagement.client_id == client_id)
        if engagement_id: q = q.filter(Engagement.id == engagement_id)
    if user_id:
        q = q.filter(Timesheet.user_id == user_id)
    return q


def csv_stream(rows: Iterable[dict], columns: list[str]) -> str:
    """Render a list of dicts to CSV (returns the full string)."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


# ─── TS-132 · My timesheet history ───────────────────────────────────

def my_history(db: Session, user: User, limit: int = 52) -> list[dict]:
    sheets = (db.query(Timesheet)
                .filter(Timesheet.user_id == user.id)
                .order_by(Timesheet.period_id.desc())
                .limit(limit).all())
    out = []
    for s in sheets:
        total    = sum(float(e.hours or 0) for e in s.entries)
        billable = sum(float(e.hours or 0) for e in s.entries if e.is_billable)
        out.append({
            "period_start": s.period.start_date.isoformat(),
            "period_end":   s.period.end_date.isoformat(),
            "status":       s.status.value,
            "entries":      len(s.entries),
            "total_hours":  round(total, 2),
            "billable_hours": round(billable, 2),
            "submitted_at": s.submitted_at.strftime("%Y-%m-%d %H:%M") if s.submitted_at else "",
            "approved_at":  s.approved_at.strftime("%Y-%m-%d %H:%M") if s.approved_at else "",
            "approver":     s.approver.display_name if s.approver else "",
        })
    return out


# ─── TS-133 · Submission compliance ──────────────────────────────────

def compliance(db: Session, tenant_id: int) -> tuple[TimesheetPeriod | None, list[dict]]:
    """Who submitted / didn't submit for the most-recent period in this
    tenant. Returns (period, rows). If no period exists yet, returns (None, [])."""
    period = (db.query(TimesheetPeriod)
                .filter(TimesheetPeriod.tenant_id == tenant_id)
                .order_by(TimesheetPeriod.start_date.desc())
                .first())
    if period is None:
        return None, []
    users = (db.query(User)
               .filter(User.tenant_id == tenant_id, User.is_active.is_(True),
                       User.role != UserRole.admin)  # admins not always required to log
               .order_by(User.display_name).all())
    # one query: every sheet for this period, indexed by user_id
    sheets = {s.user_id: s for s in
              db.query(Timesheet)
                .filter(Timesheet.tenant_id == tenant_id,
                        Timesheet.period_id == period.id).all()}
    rows = []
    for u in users:
        s = sheets.get(u.id)
        if s is None:
            status = "not_started"
            entries = 0
            hours = 0.0
        else:
            status = s.status.value
            entries = len(s.entries)
            hours = sum(float(e.hours or 0) for e in s.entries)
        rows.append({
            "user":     u.display_name,
            "email":    u.email,
            "practice": u.practice or "",
            "manager":  u.manager.display_name if u.manager else "",
            "status":   status,
            "entries":  entries,
            "hours":    round(hours, 2),
        })
    return period, rows


# ─── TS-134 · Hours by project ───────────────────────────────────────

def hours_by_project(db: Session, tenant_id: int, days: int = 30,
                     start: date | None = None, end: date | None = None,
                     client_id: int | None = None,
                     user_id: int | None = None) -> list[dict]:
    base = _entries_q(db, tenant_id, days, start=start, end=end,
                      client_id=client_id, user_id=user_id)
    # The _entries_q may already have joined Engagement (if client_id was set);
    # to keep the SELECT below correct, always join Client and re-reference Engagement
    # via the base query's join state. Using an outerjoin to Client is safe.
    rows = (base.with_entities(
                  Engagement.id.label("eng_id"),
                  Engagement.code.label("eng_code"),
                  Engagement.name.label("eng_name"),
                  Client.code.label("client_code"),
                  Client.name.label("client_name"),
                  func.sum(TimeEntry.hours).label("total"),
                  func.sum(case((TimeEntry.is_billable.is_(True), TimeEntry.hours), else_=0)).label("billable"),
              )
              .join(Engagement, Engagement.id == TimeEntry.engagement_id, isouter=False)
              .join(Client, Client.id == Engagement.client_id)
              .group_by(Engagement.id, Engagement.code, Engagement.name,
                        Client.code, Client.name)
              .order_by(func.sum(TimeEntry.hours).desc())
              .all())
    return [{
        "client":   r.client_name,
        "client_code": r.client_code,
        "project":  r.eng_name,
        "project_code": r.eng_code,
        "total_hours":    round(float(r.total or 0), 2),
        "billable_hours": round(float(r.billable or 0), 2),
        "billable_pct":   round(100 * float(r.billable or 0) / float(r.total or 1), 1) if r.total else 0,
    } for r in rows]


# ─── TS-135 · Hours by client ────────────────────────────────────────

def hours_by_client(db: Session, tenant_id: int, days: int = 30,
                    start: date | None = None, end: date | None = None,
                    client_id: int | None = None,
                    user_id: int | None = None) -> list[dict]:
    base = _entries_q(db, tenant_id, days, start=start, end=end,
                      client_id=client_id, user_id=user_id)
    rows = (base.with_entities(
                  Client.id.label("client_id"),
                  Client.code.label("client_code"),
                  Client.name.label("client_name"),
                  func.sum(TimeEntry.hours).label("total"),
                  func.sum(case((TimeEntry.is_billable.is_(True), TimeEntry.hours), else_=0)).label("billable"),
                  func.count(func.distinct(Engagement.id)).label("engagements"),
              )
              .join(Engagement, Engagement.id == TimeEntry.engagement_id, isouter=False)
              .join(Client, Client.id == Engagement.client_id)
              .group_by(Client.id, Client.code, Client.name)
              .order_by(func.sum(TimeEntry.hours).desc())
              .all())
    return [{
        "client":         r.client_name,
        "client_code":    r.client_code,
        "engagements":    r.engagements,
        "total_hours":    round(float(r.total or 0), 2),
        "billable_hours": round(float(r.billable or 0), 2),
        "billable_pct":   round(100 * float(r.billable or 0) / float(r.total or 1), 1) if r.total else 0,
    } for r in rows]


# ─── TS-136 · Billable vs non-billable (per user) ────────────────────

def billable_split(db: Session, tenant_id: int, days: int = 30,
                   start: date | None = None, end: date | None = None,
                   client_id: int | None = None,
                   user_id: int | None = None) -> list[dict]:
    base = _entries_q(db, tenant_id, days, start=start, end=end,
                      client_id=client_id, user_id=user_id)
    rows = (base.with_entities(
                  User.id.label("uid"),
                  User.display_name.label("name"),
                  User.email.label("email"),
                  User.practice.label("practice"),
                  func.sum(TimeEntry.hours).label("total"),
                  func.sum(case((TimeEntry.is_billable.is_(True), TimeEntry.hours), else_=0)).label("billable"),
              )
              .join(User, User.id == Timesheet.user_id)
              .group_by(User.id, User.display_name, User.email, User.practice)
              .order_by(func.sum(TimeEntry.hours).desc())
              .all())
    return [{
        "user":           r.name,
        "email":          r.email,
        "practice":       r.practice or "",
        "total_hours":    round(float(r.total or 0), 2),
        "billable_hours": round(float(r.billable or 0), 2),
        "non_billable":   round(float(r.total or 0) - float(r.billable or 0), 2),
        "billable_pct":   round(100 * float(r.billable or 0) / float(r.total or 1), 1) if r.total else 0,
    } for r in rows]


# ─── TS-137 · Basic utilisation ──────────────────────────────────────

def utilisation(db: Session, tenant_id: int, days: int = 30,
                daily_hours: float = 8.0,
                start: date | None = None, end: date | None = None,
                client_id: int | None = None,
                user_id: int | None = None) -> list[dict]:
    """Billable hours ÷ available business hours over the window."""
    s, e = resolve_window(days, start, end)
    holiday_dates = {h.holiday_date for h in
                     db.query(Holiday)
                       .filter(Holiday.tenant_id == tenant_id,
                               Holiday.holiday_date >= s,
                               Holiday.holiday_date <= e).all()}
    business_days = 0
    d = s
    while d <= e:
        if d.weekday() < 5 and d not in holiday_dates:
            business_days += 1
        d += timedelta(days=1)
    available = business_days * daily_hours

    split = billable_split(db, tenant_id, days, start=start, end=end,
                           client_id=client_id, user_id=user_id)
    out = []
    for r in split:
        out.append({
            "user":             r["user"],
            "email":            r["email"],
            "practice":         r["practice"],
            "billable_hours":   r["billable_hours"],
            "available_hours":  available,
            "utilisation_pct":  round(100 * r["billable_hours"] / available, 1) if available else 0,
            "total_hours":      r["total_hours"],
        })
    out.sort(key=lambda r: r["utilisation_pct"], reverse=True)
    return out
