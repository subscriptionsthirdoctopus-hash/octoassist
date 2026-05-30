"""Reporting service — pure aggregate queries on top of the existing models.

All functions take `tenant_id` first and return small Python data structures
that the views render. Postgres-specific date_trunc is used for time series.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..models import (
    Agent,
    AssetSnapshot,
    Category,
    Change,
    ChangeStatus,
    ChangeType,
    Problem,
    ProblemStatus,
    Ticket,
    TicketKind,
    TicketPriority,
    TicketStatus,
    User,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ----------  KPI summary ----------

def kpi_summary(db: Session, tenant_id: int, *, days: int = 30, priority: str | None = None, category_id: int | None = None) -> dict:
    """Top-level numbers for the dashboard."""
    now = _now()
    cutoff_date = now - timedelta(days=days)

    q_open = db.query(func.count(Ticket.id)).filter(
        Ticket.tenant_id == tenant_id,
        Ticket.status.in_([TicketStatus.open, TicketStatus.in_progress, TicketStatus.on_hold])
    )
    if priority:
        q_open = q_open.filter(Ticket.priority == TicketPriority(priority))
    if category_id:
        q_open = q_open.filter(Ticket.category_id == category_id)
    tickets_open = q_open.scalar() or 0

    q_risk = db.query(func.count(Ticket.id)).filter(
        Ticket.tenant_id == tenant_id,
        Ticket.status.in_([TicketStatus.open, TicketStatus.in_progress, TicketStatus.on_hold]),
        Ticket.due_resolution_at.isnot(None),
        Ticket.due_resolution_at < now
    )
    if priority:
        q_risk = q_risk.filter(Ticket.priority == TicketPriority(priority))
    if category_id:
        q_risk = q_risk.filter(Ticket.category_id == category_id)
    tickets_at_risk = q_risk.scalar() or 0

    q_res = db.query(func.count(Ticket.id)).filter(
        Ticket.tenant_id == tenant_id,
        Ticket.resolved_at.isnot(None),
        Ticket.resolved_at >= cutoff_date
    )
    if priority:
        q_res = q_res.filter(Ticket.priority == TicketPriority(priority))
    if category_id:
        q_res = q_res.filter(Ticket.category_id == category_id)
    tickets_resolved_7d = q_res.scalar() or 0

    # Mean time to resolution (minutes) for tickets resolved in dynamic period
    q_mttr = db.query(func.avg(func.extract("epoch", Ticket.resolved_at - Ticket.created_at))).filter(
        Ticket.tenant_id == tenant_id,
        Ticket.resolved_at.isnot(None),
        Ticket.resolved_at >= cutoff_date
    )
    if priority:
        q_mttr = q_mttr.filter(Ticket.priority == TicketPriority(priority))
    if category_id:
        q_mttr = q_mttr.filter(Ticket.category_id == category_id)
    mttr_seconds = q_mttr.scalar()
    mttr_minutes = round((mttr_seconds or 0) / 60)

    changes_in_flight = (db.query(func.count(Change.id))
                           .filter(Change.tenant_id == tenant_id,
                                   Change.status.in_([ChangeStatus.under_review,
                                                      ChangeStatus.approved,
                                                      ChangeStatus.in_progress]))
                           .scalar() or 0)

    problems_open = (db.query(func.count(Problem.id))
                       .filter(Problem.tenant_id == tenant_id,
                               Problem.status.notin_([ProblemStatus.resolved, ProblemStatus.closed]))
                       .scalar() or 0)

    assets_total = (db.query(func.count(Agent.id))
                      .filter(Agent.tenant_id == tenant_id)
                      .scalar() or 0)

    one_day_ago = now - timedelta(days=1)
    assets_seen_24h = (db.query(func.count(Agent.id))
                         .filter(Agent.tenant_id == tenant_id,
                                 Agent.last_seen_at >= one_day_ago)
                         .scalar() or 0)

    return {
        "tickets_open":         tickets_open,
        "tickets_at_risk":      tickets_at_risk,
        "tickets_resolved_7d":  tickets_resolved_7d,
        "mttr_minutes":         mttr_minutes,
        "mttr_human":           _humanize_minutes(mttr_minutes),
        "changes_in_flight":    changes_in_flight,
        "problems_open":        problems_open,
        "assets_total":         assets_total,
        "assets_seen_24h":      assets_seen_24h,
        "asset_coverage_pct":   round(100 * assets_seen_24h / assets_total) if assets_total else 0,
    }


def _humanize_minutes(m: int) -> str:
    if m <= 0:
        return "—"
    if m < 60:
        return f"{m}m"
    h, mm = divmod(m, 60)
    if h < 24:
        return f"{h}h {mm}m" if mm else f"{h}h"
    d, hh = divmod(h, 24)
    return f"{d}d {hh}h" if hh else f"{d}d"


# ----------  Ticket breakdowns ----------

def tickets_by_status(db: Session, tenant_id: int, *, days: int | None = None, priority: str | None = None, category_id: int | None = None) -> list[tuple[str, int]]:
    q = db.query(Ticket.status, func.count(Ticket.id)).filter(Ticket.tenant_id == tenant_id)
    if days:
        cutoff = _now() - timedelta(days=days)
        q = q.filter(Ticket.created_at >= cutoff)
    if priority:
        q = q.filter(Ticket.priority == TicketPriority(priority))
    if category_id:
        q = q.filter(Ticket.category_id == category_id)
    rows = q.group_by(Ticket.status).all()
    return [(r[0].value, r[1]) for r in rows]


def tickets_by_priority(db: Session, tenant_id: int, *, only_open: bool = True, days: int | None = None, priority: str | None = None, category_id: int | None = None) -> list[tuple[str, int]]:
    q = db.query(Ticket.priority, func.count(Ticket.id)).filter(Ticket.tenant_id == tenant_id)
    if only_open:
        q = q.filter(Ticket.status.in_([TicketStatus.open, TicketStatus.in_progress, TicketStatus.on_hold]))
    if days:
        cutoff = _now() - timedelta(days=days)
        q = q.filter(Ticket.created_at >= cutoff)
    if priority:
        q = q.filter(Ticket.priority == TicketPriority(priority))
    if category_id:
        q = q.filter(Ticket.category_id == category_id)
    q = q.group_by(Ticket.priority)
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    rows = [(r[0].value, r[1]) for r in q.all()]
    return sorted(rows, key=lambda x: order.get(x[0], 9))


def tickets_by_kind(db: Session, tenant_id: int) -> list[tuple[str, int]]:
    rows = (db.query(Ticket.kind, func.count(Ticket.id))
              .filter(Ticket.tenant_id == tenant_id)
              .group_by(Ticket.kind).all())
    return [(r[0].value.replace("_", " "), r[1]) for r in rows]


def tickets_by_category(db: Session, tenant_id: int, *, days: int = 30, top: int = 10, priority: str | None = None, category_id: int | None = None) -> list[tuple[str, int]]:
    cutoff = _now() - timedelta(days=days)
    q = (db.query(Category.name, func.count(Ticket.id))
              .join(Ticket, Ticket.category_id == Category.id)
              .filter(Ticket.tenant_id == tenant_id, Ticket.created_at >= cutoff))
    if priority:
        q = q.filter(Ticket.priority == TicketPriority(priority))
    if category_id:
        q = q.filter(Ticket.category_id == category_id)
    rows = (q.group_by(Category.name)
              .order_by(func.count(Ticket.id).desc())
              .limit(top).all())
    return [(r[0], r[1]) for r in rows]


def tickets_per_day(db: Session, tenant_id: int, *, days: int = 30, priority: str | None = None, category_id: int | None = None) -> list[tuple[str, int]]:
    """Return one entry per day for the last `days` days, even if 0."""
    cutoff_date = (_now() - timedelta(days=days - 1)).date()
    q = (db.query(func.date_trunc("day", Ticket.created_at).label("d"),
                     func.count(Ticket.id))
              .filter(Ticket.tenant_id == tenant_id, Ticket.created_at >= cutoff_date))
    if priority:
        q = q.filter(Ticket.priority == TicketPriority(priority))
    if category_id:
        q = q.filter(Ticket.category_id == category_id)
    rows = q.group_by("d").order_by("d").all()
    bucket: dict[date, int] = {}
    for d, c in rows:
        if d is not None:
            bucket[d.date()] = int(c)
    out: list[tuple[str, int]] = []
    for i in range(days):
        d = cutoff_date + timedelta(days=i)
        out.append((d.strftime("%b %d"), bucket.get(d, 0)))
    return out


def workload_by_assignee(db: Session, tenant_id: int, *, top: int = 10, days: int | None = None, priority: str | None = None, category_id: int | None = None) -> list[tuple[str, int]]:
    q = (db.query(User.full_name, User.email, func.count(Ticket.id))
              .join(Ticket, Ticket.assignee_id == User.id)
              .filter(Ticket.tenant_id == tenant_id,
                      Ticket.status.in_([TicketStatus.open, TicketStatus.in_progress, TicketStatus.on_hold])))
    if days:
        cutoff = _now() - timedelta(days=days)
        q = q.filter(Ticket.created_at >= cutoff)
    if priority:
        q = q.filter(Ticket.priority == TicketPriority(priority))
    if category_id:
        q = q.filter(Ticket.category_id == category_id)
    rows = (q.group_by(User.id, User.full_name, User.email)
              .order_by(func.count(Ticket.id).desc())
              .limit(top).all())
    return [(r[0] or r[1], r[2]) for r in rows]


def top_reporters(db: Session, tenant_id: int, *, days: int = 30, top: int = 10) -> list[tuple[str, int]]:
    cutoff = _now() - timedelta(days=days)
    rows = (db.query(User.full_name, User.email, func.count(Ticket.id))
              .join(Ticket, Ticket.reporter_id == User.id)
              .filter(Ticket.tenant_id == tenant_id, Ticket.created_at >= cutoff)
              .group_by(User.id, User.full_name, User.email)
              .order_by(func.count(Ticket.id).desc())
              .limit(top).all())
    return [(r[0] or r[1], r[2]) for r in rows]


# ----------  SLA ----------

def sla_compliance(db: Session, tenant_id: int, *, days: int = 30, priority: str | None = None, category_id: int | None = None) -> dict:
    """% of tickets resolved within their resolution SLA in the last N days."""
    cutoff = _now() - timedelta(days=days)
    q = (db.query(
                func.count(Ticket.id).label("total"),
                func.sum(case((Ticket.resolved_at <= Ticket.due_resolution_at, 1), else_=0)).label("within"),
                func.sum(case((Ticket.first_response_at <= Ticket.due_response_at, 1), else_=0)).label("response_within"),
                func.count(case((Ticket.first_response_at.isnot(None), 1))).label("response_total"),
            )
            .filter(Ticket.tenant_id == tenant_id,
                    Ticket.resolved_at.isnot(None),
                    Ticket.resolved_at >= cutoff,
                    Ticket.due_resolution_at.isnot(None)))
    if priority:
        q = q.filter(Ticket.priority == TicketPriority(priority))
    if category_id:
        q = q.filter(Ticket.category_id == category_id)
    row = q.one()
    total = int(row.total or 0)
    within = int(row.within or 0)
    resp_total = int(row.response_total or 0)
    resp_within = int(row.response_within or 0)
    return {
        "total_resolved": total,
        "within": within,
        "resolution_within": within,
        "resolution_pct": (round(100 * within / total) if total else 0),
        "response_total": resp_total,
        "response_within": resp_within,
        "response_pct": (round(100 * resp_within / resp_total) if resp_total else 0),
    }


def sla_breaches(db: Session, tenant_id: int, *, limit: int = 50) -> list[Ticket]:
    """Currently breached open tickets (past resolution SLA)."""
    now = _now()
    return (db.query(Ticket)
              .filter(Ticket.tenant_id == tenant_id,
                      Ticket.status.in_([TicketStatus.open, TicketStatus.in_progress, TicketStatus.on_hold]),
                      Ticket.due_resolution_at.isnot(None),
                      Ticket.due_resolution_at < now)
              .order_by(Ticket.due_resolution_at)
              .limit(limit).all())


def sla_compliance_by_priority(db: Session, tenant_id: int, *, days: int = 30) -> list[dict]:
    """Per-priority SLA compliance."""
    cutoff = _now() - timedelta(days=days)
    rows = (db.query(
                Ticket.priority,
                func.count(Ticket.id).label("total"),
                func.sum(case((Ticket.resolved_at <= Ticket.due_resolution_at, 1), else_=0)).label("within"),
            )
            .filter(Ticket.tenant_id == tenant_id,
                    Ticket.resolved_at.isnot(None),
                    Ticket.resolved_at >= cutoff,
                    Ticket.due_resolution_at.isnot(None))
            .group_by(Ticket.priority)
            .all())
    out = []
    for p, total, within in rows:
        total = int(total or 0)
        within = int(within or 0)
        out.append({
            "priority": p.value,
            "total": total,
            "within": within,
            "pct": (round(100 * within / total) if total else 0),
        })
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(out, key=lambda r: order.get(r["priority"], 9))


# ----------  Asset ----------

def asset_os_breakdown(db: Session, tenant_id: int) -> list[tuple[str, int]]:
    """Group endpoints by OS caption from their latest snapshot.

    Group in Python rather than SQL — JSONB-path GROUP-BY in SQLAlchemy 2 +
    psycopg3 emits two parameter bindings for the same expression which
    Postgres rejects ("must appear in the GROUP BY clause"). Set sizes are
    bounded by endpoint count, so this is cheap.
    """
    sub = (db.query(AssetSnapshot.agent_id,
                    func.max(AssetSnapshot.snapshot_at).label("latest"))
             .group_by(AssetSnapshot.agent_id).subquery())
    snaps = (db.query(AssetSnapshot.payload)
               .join(sub, (AssetSnapshot.agent_id == sub.c.agent_id) & (AssetSnapshot.snapshot_at == sub.c.latest))
               .join(Agent, Agent.id == AssetSnapshot.agent_id)
               .filter(Agent.tenant_id == tenant_id, Agent.uninstall_pending.is_(False)).all())
    counts: dict[str, int] = {}
    for (payload,) in snaps:
        caption = ((payload or {}).get("os") or {}).get("caption") or "Unknown"
        counts[caption] = counts.get(caption, 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)


def asset_software_top(db: Session, tenant_id: int, *, top: int = 20) -> list[tuple[str, int]]:
    """Most-installed software across all latest endpoint snapshots."""
    sub = (db.query(AssetSnapshot.agent_id,
                    func.max(AssetSnapshot.snapshot_at).label("latest"))
             .group_by(AssetSnapshot.agent_id).subquery())
    snaps = (db.query(AssetSnapshot.payload)
               .join(sub, (AssetSnapshot.agent_id == sub.c.agent_id) & (AssetSnapshot.snapshot_at == sub.c.latest))
               .join(Agent, Agent.id == AssetSnapshot.agent_id)
               .filter(Agent.tenant_id == tenant_id, Agent.uninstall_pending.is_(False)).all())
    counts: dict[str, int] = {}
    for (payload,) in snaps:
        for sw in (payload or {}).get("software", []):
            name = sw.get("name", "")
            if not name:
                continue
            counts[name] = counts.get(name, 0) + 1
    items = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return items[:top]


def asset_ram_distribution(db: Session, tenant_id: int) -> list[tuple[str, int]]:
    sub = (db.query(AssetSnapshot.agent_id,
                    func.max(AssetSnapshot.snapshot_at).label("latest"))
             .group_by(AssetSnapshot.agent_id).subquery())
    snaps = (db.query(AssetSnapshot.payload)
               .join(sub, (AssetSnapshot.agent_id == sub.c.agent_id) & (AssetSnapshot.snapshot_at == sub.c.latest))
               .join(Agent, Agent.id == AssetSnapshot.agent_id)
               .filter(Agent.tenant_id == tenant_id, Agent.uninstall_pending.is_(False)).all())
    buckets = {"≤8 GB": 0, "16 GB": 0, "32 GB": 0, ">32 GB": 0}
    for (payload,) in snaps:
        ram = (payload or {}).get("memory", {}).get("total_gb")
        if ram is None:
            continue
        ram = float(ram)
        if ram <= 8.5:
            buckets["≤8 GB"] += 1
        elif ram <= 16.5:
            buckets["16 GB"] += 1
        elif ram <= 32.5:
            buckets["32 GB"] += 1
        else:
            buckets[">32 GB"] += 1
    return [(k, v) for k, v in buckets.items() if v > 0]


# ----------  Change ----------

def changes_by_status(db: Session, tenant_id: int) -> list[tuple[str, int]]:
    rows = (db.query(Change.status, func.count(Change.id))
              .filter(Change.tenant_id == tenant_id)
              .group_by(Change.status).all())
    return [(r[0].value, r[1]) for r in rows]


def changes_by_type(db: Session, tenant_id: int) -> list[tuple[str, int]]:
    rows = (db.query(Change.change_type, func.count(Change.id))
              .filter(Change.tenant_id == tenant_id)
              .group_by(Change.change_type).all())
    return [(r[0].value, r[1]) for r in rows]


def upcoming_changes(db: Session, tenant_id: int, *, limit: int = 20) -> list[Change]:
    return (db.query(Change)
              .filter(Change.tenant_id == tenant_id,
                      Change.status.in_([ChangeStatus.approved, ChangeStatus.in_progress]),
                      Change.planned_start.isnot(None))
              .order_by(Change.planned_start)
              .limit(limit).all())


def change_throughput(db: Session, tenant_id: int, *, days: int = 30) -> dict:
    """Counts of changes created/completed in last N days."""
    cutoff = _now() - timedelta(days=days)
    created = (db.query(func.count(Change.id))
                 .filter(Change.tenant_id == tenant_id, Change.created_at >= cutoff)
                 .scalar() or 0)
    completed = (db.query(func.count(Change.id))
                   .filter(Change.tenant_id == tenant_id,
                           Change.status == ChangeStatus.completed,
                           Change.actual_end.isnot(None),
                           Change.actual_end >= cutoff)
                   .scalar() or 0)
    failed = (db.query(func.count(Change.id))
                .filter(Change.tenant_id == tenant_id,
                        Change.status == ChangeStatus.failed,
                        Change.actual_end.isnot(None),
                        Change.actual_end >= cutoff)
                .scalar() or 0)
    return {"created": created, "completed": completed, "failed": failed,
            "success_rate": (round(100 * completed / (completed + failed)) if (completed + failed) else 0)}
