"""Patch Management — fleet-level and per-endpoint queries."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..models import Agent, PatchAvailable, PatchSeverity


def _now() -> datetime:
    return datetime.now(timezone.utc)


def fleet_patch_summary(db: Session, tenant_id: int) -> list[dict]:
    """Per-endpoint patch posture, sorted worst-first (most criticals)."""
    rows = (db.query(
                Agent.id, Agent.hostname, Agent.last_seen_at,
                func.count(PatchAvailable.id).label("total"),
                func.sum(case((PatchAvailable.severity == PatchSeverity.critical, 1), else_=0)).label("critical"),
                func.sum(case((PatchAvailable.severity == PatchSeverity.important, 1), else_=0)).label("important"),
                func.sum(case((PatchAvailable.severity == PatchSeverity.moderate, 1), else_=0)).label("moderate"),
            )
            .outerjoin(PatchAvailable, PatchAvailable.agent_id == Agent.id)
            .filter(Agent.tenant_id == tenant_id)
            .group_by(Agent.id, Agent.hostname, Agent.last_seen_at)
            .all())
    out = []
    for r in rows:
        out.append({
            "agent_id":  r[0],
            "hostname":  r[1],
            "last_seen": r[2],
            "total":     int(r[3] or 0),
            "critical":  int(r[4] or 0),
            "important": int(r[5] or 0),
            "moderate":  int(r[6] or 0),
        })
    out.sort(key=lambda x: (-x["critical"], -x["important"], -x["total"], x["hostname"]))
    return out


def severity_breakdown(db: Session, tenant_id: int) -> list[tuple[str, int]]:
    rows = (db.query(PatchAvailable.severity, func.count(PatchAvailable.id))
              .join(Agent, Agent.id == PatchAvailable.agent_id)
              .filter(Agent.tenant_id == tenant_id)
              .group_by(PatchAvailable.severity)
              .all())
    order = {"critical": 0, "important": 1, "moderate": 2, "low": 3, "unknown": 4}
    return sorted([(s.value, int(c)) for s, c in rows], key=lambda x: order.get(x[0], 9))


def top_missing_packages(db: Session, tenant_id: int, *, top: int = 20) -> list[tuple[str, int]]:
    rows = (db.query(PatchAvailable.package_name, func.count(PatchAvailable.id))
              .join(Agent, Agent.id == PatchAvailable.agent_id)
              .filter(Agent.tenant_id == tenant_id)
              .group_by(PatchAvailable.package_name)
              .order_by(func.count(PatchAvailable.id).desc())
              .limit(top)
              .all())
    return [(r[0], int(r[1])) for r in rows]


def patches_for_agent(db: Session, agent_id: int) -> list[PatchAvailable]:
    sev_order = case(
        (PatchAvailable.severity == PatchSeverity.critical, 0),
        (PatchAvailable.severity == PatchSeverity.important, 1),
        (PatchAvailable.severity == PatchSeverity.moderate, 2),
        (PatchAvailable.severity == PatchSeverity.low, 3),
        else_=4,
    )
    return (db.query(PatchAvailable)
              .filter(PatchAvailable.agent_id == agent_id)
              .order_by(sev_order, PatchAvailable.package_name)
              .all())


def patch_kpis(db: Session, tenant_id: int) -> dict:
    """KPIs surfaced on the main dashboard."""
    total_endpoints = db.query(func.count(Agent.id)).filter(Agent.tenant_id == tenant_id).scalar() or 0

    # Endpoints with at least 1 critical patch outstanding
    bad_eps = (db.query(func.count(func.distinct(PatchAvailable.agent_id)))
                 .join(Agent, Agent.id == PatchAvailable.agent_id)
                 .filter(Agent.tenant_id == tenant_id,
                         PatchAvailable.severity == PatchSeverity.critical)
                 .scalar()) or 0

    compliant = max(0, total_endpoints - bad_eps)
    pct = round(100 * compliant / total_endpoints) if total_endpoints else 100

    total_critical = (db.query(func.count(PatchAvailable.id))
                        .join(Agent, Agent.id == PatchAvailable.agent_id)
                        .filter(Agent.tenant_id == tenant_id,
                                PatchAvailable.severity == PatchSeverity.critical)
                        .scalar()) or 0

    total_patches = (db.query(func.count(PatchAvailable.id))
                       .join(Agent, Agent.id == PatchAvailable.agent_id)
                       .filter(Agent.tenant_id == tenant_id)
                       .scalar()) or 0

    return {
        "total_endpoints":   int(total_endpoints),
        "compliant_endpoints": int(compliant),
        "non_compliant":     int(bad_eps),
        "compliance_pct":    int(pct),
        "total_critical":    int(total_critical),
        "total_patches":     int(total_patches),
    }
