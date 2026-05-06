"""Patch Management — fleet view, aging history, deployment windows."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..models import (
    Agent,
    PatchObservation,
    PatchSeverity,
    PatchWindow,
    PatchWindowStatus,
    PatchWindowTarget,
    PatchWindowTargetStatus,
    User,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------- "currently missing"

def fleet_patch_summary(db: Session, tenant_id: int) -> list[dict]:
    """Per-endpoint patch posture (currently-missing only). Sorted worst-first."""
    rows = (db.query(
                Agent.id, Agent.hostname, Agent.last_seen_at,
                func.count(PatchObservation.id).label("total"),
                func.sum(case((PatchObservation.severity == PatchSeverity.critical, 1), else_=0)).label("critical"),
                func.sum(case((PatchObservation.severity == PatchSeverity.important, 1), else_=0)).label("important"),
                func.sum(case((PatchObservation.severity == PatchSeverity.moderate, 1), else_=0)).label("moderate"),
                func.min(PatchObservation.first_seen_at).label("oldest_seen"),
            )
            .outerjoin(PatchObservation,
                       (PatchObservation.agent_id == Agent.id) &
                       (PatchObservation.resolved_at.is_(None)))
            .filter(Agent.tenant_id == tenant_id)
            .group_by(Agent.id, Agent.hostname, Agent.last_seen_at)
            .all())
    out = []
    now = _now()
    for r in rows:
        oldest = r[7]
        oldest_age_days = int((now - oldest).total_seconds() / 86400) if oldest else None
        out.append({
            "agent_id":  r[0],
            "hostname":  r[1],
            "last_seen": r[2],
            "total":     int(r[3] or 0),
            "critical":  int(r[4] or 0),
            "important": int(r[5] or 0),
            "moderate":  int(r[6] or 0),
            "oldest_age_days": oldest_age_days,
        })
    out.sort(key=lambda x: (-x["critical"], -x["important"], -x["total"], x["hostname"]))
    return out


def severity_breakdown(db: Session, tenant_id: int) -> list[tuple[str, int]]:
    rows = (db.query(PatchObservation.severity, func.count(PatchObservation.id))
              .join(Agent, Agent.id == PatchObservation.agent_id)
              .filter(Agent.tenant_id == tenant_id,
                      PatchObservation.resolved_at.is_(None))
              .group_by(PatchObservation.severity)
              .all())
    order = {"critical": 0, "important": 1, "moderate": 2, "low": 3, "unknown": 4}
    return sorted([(s.value, int(c)) for s, c in rows], key=lambda x: order.get(x[0], 9))


def top_missing_packages(db: Session, tenant_id: int, *, top: int = 20) -> list[tuple[str, int]]:
    rows = (db.query(PatchObservation.package_name, func.count(PatchObservation.id))
              .join(Agent, Agent.id == PatchObservation.agent_id)
              .filter(Agent.tenant_id == tenant_id,
                      PatchObservation.resolved_at.is_(None))
              .group_by(PatchObservation.package_name)
              .order_by(func.count(PatchObservation.id).desc())
              .limit(top)
              .all())
    return [(r[0], int(r[1])) for r in rows]


def patches_for_agent(db: Session, agent_id: int) -> list[PatchObservation]:
    sev_order = case(
        (PatchObservation.severity == PatchSeverity.critical, 0),
        (PatchObservation.severity == PatchSeverity.important, 1),
        (PatchObservation.severity == PatchSeverity.moderate, 2),
        (PatchObservation.severity == PatchSeverity.low, 3),
        else_=4,
    )
    return (db.query(PatchObservation)
              .filter(PatchObservation.agent_id == agent_id,
                      PatchObservation.resolved_at.is_(None))
              .order_by(sev_order, PatchObservation.first_seen_at, PatchObservation.package_name)
              .all())


def patch_kpis(db: Session, tenant_id: int) -> dict:
    total_endpoints = db.query(func.count(Agent.id)).filter(Agent.tenant_id == tenant_id).scalar() or 0
    bad_eps = (db.query(func.count(func.distinct(PatchObservation.agent_id)))
                 .join(Agent, Agent.id == PatchObservation.agent_id)
                 .filter(Agent.tenant_id == tenant_id,
                         PatchObservation.resolved_at.is_(None),
                         PatchObservation.severity == PatchSeverity.critical)
                 .scalar()) or 0
    compliant = max(0, total_endpoints - bad_eps)
    pct = round(100 * compliant / total_endpoints) if total_endpoints else 100

    total_critical = (db.query(func.count(PatchObservation.id))
                        .join(Agent, Agent.id == PatchObservation.agent_id)
                        .filter(Agent.tenant_id == tenant_id,
                                PatchObservation.resolved_at.is_(None),
                                PatchObservation.severity == PatchSeverity.critical)
                        .scalar()) or 0

    total_patches = (db.query(func.count(PatchObservation.id))
                       .join(Agent, Agent.id == PatchObservation.agent_id)
                       .filter(Agent.tenant_id == tenant_id,
                               PatchObservation.resolved_at.is_(None))
                       .scalar()) or 0

    return {
        "total_endpoints":     int(total_endpoints),
        "compliant_endpoints": int(compliant),
        "non_compliant":       int(bad_eps),
        "compliance_pct":      int(pct),
        "total_critical":      int(total_critical),
        "total_patches":       int(total_patches),
    }


# ---------------------------------------------------------- aging

def aging_buckets(db: Session, tenant_id: int) -> dict:
    now = _now()
    rows = (db.query(PatchObservation)
              .join(Agent, Agent.id == PatchObservation.agent_id)
              .filter(Agent.tenant_id == tenant_id,
                      PatchObservation.resolved_at.is_(None))
              .all())

    buckets = {
        "0-7d":   {"total": 0, "critical": 0, "important": 0},
        "8-30d":  {"total": 0, "critical": 0, "important": 0},
        "31-90d": {"total": 0, "critical": 0, "important": 0},
        ">90d":   {"total": 0, "critical": 0, "important": 0},
    }
    oldest_critical: list[dict] = []
    for o in rows:
        age = (now - o.first_seen_at).total_seconds() / 86400
        if   age <= 7:    bk = "0-7d"
        elif age <= 30:   bk = "8-30d"
        elif age <= 90:   bk = "31-90d"
        else:             bk = ">90d"
        buckets[bk]["total"] += 1
        if o.severity == PatchSeverity.critical:
            buckets[bk]["critical"] += 1
            oldest_critical.append({
                "agent_id":   o.agent_id,
                "hostname":   o.agent.hostname if o.agent else "",
                "package":    o.package_name,
                "version":    o.available_version,
                "first_seen": o.first_seen_at,
                "age_days":   int(age),
            })
        elif o.severity == PatchSeverity.important:
            buckets[bk]["important"] += 1

    oldest_critical.sort(key=lambda x: -x["age_days"])
    return {
        "buckets": buckets,
        "oldest_critical": oldest_critical[:25],
        "total_unresolved": sum(b["total"] for b in buckets.values()),
    }


def recently_resolved(db: Session, tenant_id: int, *, days: int = 30, limit: int = 50) -> list[PatchObservation]:
    cutoff = _now() - timedelta(days=days)
    return (db.query(PatchObservation)
              .join(Agent, Agent.id == PatchObservation.agent_id)
              .filter(Agent.tenant_id == tenant_id,
                      PatchObservation.resolved_at.isnot(None),
                      PatchObservation.resolved_at >= cutoff)
              .order_by(PatchObservation.resolved_at.desc())
              .limit(limit).all())


# ---------------------------------------------------------- deployment windows

def list_windows(db: Session, tenant_id: int) -> list[PatchWindow]:
    return (db.query(PatchWindow)
              .filter(PatchWindow.tenant_id == tenant_id)
              .order_by(PatchWindow.created_at.desc())
              .all())


def create_window(
    db: Session, *,
    tenant_id: int,
    creator: User,
    name: str,
    description: str = "",
    severity_filter: PatchSeverity | None = None,
    hostname_pattern: str = "%",
    scheduled_for: datetime | None = None,
    notes: str = "",
) -> PatchWindow:
    win = PatchWindow(
        tenant_id=tenant_id,
        name=name.strip()[:160],
        description=description.strip(),
        severity_filter=severity_filter,
        hostname_pattern=(hostname_pattern.strip() or "%")[:120],
        scheduled_for=scheduled_for,
        notes=notes.strip(),
        created_by_id=creator.id,
        status=PatchWindowStatus.draft,
    )
    db.add(win)
    db.flush()
    _materialise_targets(db, win)
    db.commit()
    db.refresh(win)
    return win


def _materialise_targets(db: Session, win: PatchWindow) -> int:
    """Snapshot the matching endpoints + missing-patch counts at planning time."""
    agents = (db.query(Agent)
                .filter(Agent.tenant_id == win.tenant_id,
                        Agent.hostname.like(win.hostname_pattern))
                .all())

    counts: dict[int, int] = {}
    q = (db.query(PatchObservation.agent_id, func.count(PatchObservation.id))
           .join(Agent, Agent.id == PatchObservation.agent_id)
           .filter(Agent.tenant_id == win.tenant_id,
                   PatchObservation.resolved_at.is_(None)))
    if win.severity_filter is not None:
        q = q.filter(PatchObservation.severity == win.severity_filter)
    for aid, c in q.group_by(PatchObservation.agent_id).all():
        counts[int(aid)] = int(c)

    added = 0
    for a in agents:
        c = counts.get(a.id, 0)
        if c == 0:
            continue
        db.add(PatchWindowTarget(
            window_id=win.id,
            agent_id=a.id,
            status=PatchWindowTargetStatus.planned,
            missing_at_plan=c,
        ))
        added += 1
    return added


def transition_window(db: Session, *, window: PatchWindow, new_status: PatchWindowStatus) -> PatchWindow:
    if window.status == new_status:
        return window
    now = _now()
    window.status = new_status
    if new_status == PatchWindowStatus.in_progress and window.started_at is None:
        window.started_at = now
    if new_status == PatchWindowStatus.completed and window.completed_at is None:
        window.completed_at = now
    if new_status == PatchWindowStatus.cancelled and window.cancelled_at is None:
        window.cancelled_at = now
    db.commit()
    db.refresh(window)
    return window


def update_target_status(
    db: Session, *,
    target: PatchWindowTarget,
    new_status: PatchWindowTargetStatus,
    actor: User | None = None,
    note: str = "",
) -> PatchWindowTarget:
    target.status = new_status
    target.actor_id = actor.id if actor else None
    if note:
        target.note = note
    db.commit()
    db.refresh(target)
    return target


def window_progress(window: PatchWindow) -> dict:
    counts = {s.value: 0 for s in PatchWindowTargetStatus}
    for t in window.targets:
        counts[t.status.value] = counts.get(t.status.value, 0) + 1
    total = sum(counts.values())
    done = counts.get("succeeded", 0) + counts.get("skipped", 0) + counts.get("failed", 0)
    pct = round(100 * done / total) if total else 0
    return {"counts": counts, "total": total, "done": done, "pct": pct}


def window_candidate_packages(db: Session, window: PatchWindow) -> list[dict]:
    """Distinct missing packages across this window's targets.

    Each row: {name, severity, count, sources, hostnames_sample, selected}.
    Sorted by severity (critical first) then count descending.
    """
    target_agent_ids = [t.agent_id for t in window.targets]
    if not target_agent_ids:
        return []

    q = (db.query(PatchObservation)
           .filter(PatchObservation.agent_id.in_(target_agent_ids),
                   PatchObservation.resolved_at.is_(None)))
    if window.severity_filter is not None:
        q = q.filter(PatchObservation.severity == window.severity_filter)

    by_pkg: dict[str, dict] = {}
    for o in q.all():
        d = by_pkg.setdefault(o.package_name, {
            "name": o.package_name,
            "severity": o.severity.value,
            "count": 0,
            "sources": set(),
            "hostnames": [],
            "title": o.title,
        })
        d["count"] += 1
        d["sources"].add(o.source)
        if o.agent and o.agent.hostname not in d["hostnames"]:
            d["hostnames"].append(o.agent.hostname)
        # Promote severity to most-severe seen across endpoints
        sev_rank = {"critical": 0, "important": 1, "moderate": 2, "low": 3, "unknown": 4}
        if sev_rank.get(o.severity.value, 9) < sev_rank.get(d["severity"], 9):
            d["severity"] = o.severity.value

    selected_set = set(window.selected_packages or [])
    out = []
    for d in by_pkg.values():
        out.append({
            "name": d["name"],
            "severity": d["severity"],
            "count": d["count"],
            "sources": ", ".join(sorted(d["sources"])),
            "hostnames_sample": ", ".join(d["hostnames"][:5]) + ("…" if len(d["hostnames"]) > 5 else ""),
            "title": d["title"] or "",
            "selected": d["name"] in selected_set,
        })
    sev_rank = {"critical": 0, "important": 1, "moderate": 2, "low": 3, "unknown": 4}
    out.sort(key=lambda r: (sev_rank.get(r["severity"], 9), -r["count"], r["name"]))
    return out
