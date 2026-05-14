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

    # Phase 8: notify on important transitions.
    from . import notifications  # local import to avoid circular import at module load
    if new_status == PatchWindowStatus.in_progress:
        notifications.patch_window_started(db, window)
    elif new_status == PatchWindowStatus.completed:
        notifications.patch_window_completed(db, window)

    return window


def quick_deploy_category(
    db: Session, *,
    tenant_id: int,
    creator: User,
    severity: PatchSeverity | None = None,
    vendor: str | None = None,
) -> PatchWindow:
    """Intune / Zoho Endpoint Central-style one-click deploy.

    Creates a patch window in `in_progress` with auto_execute=True and
    auto-selects all matching candidate packages, so the agent picks the
    job up on its next check-in (no further admin action required).

    Either `severity` or `vendor` must be set. The window's name and
    description are auto-generated from the chosen filter + IST timestamp.
    """
    from datetime import timezone as _tz, timedelta as _td

    if severity is None and not vendor:
        raise ValueError("quick_deploy_category requires severity or vendor")

    IST = _tz(_td(hours=5, minutes=30), name="IST")
    now_ist = _now().astimezone(IST).strftime("%Y-%m-%d %H:%M IST")

    if severity is not None:
        label = severity.value.title()
        name = f"Quick deploy — {label} severity — {now_ist}"
        desc = (f"Auto-created one-click deployment for all currently-missing "
                f"{severity.value} patches across the fleet. Started immediately, "
                f"auto-executes on next agent check-in.")
    else:
        name = f"Quick deploy — {vendor} updates — {now_ist}"
        desc = (f"Auto-created one-click deployment for all currently-missing "
                f"{vendor} patches across the fleet. Started immediately, "
                f"auto-executes on next agent check-in.")

    win = create_window(
        db,
        tenant_id=tenant_id, creator=creator,
        name=name, description=desc,
        severity_filter=severity,
        hostname_pattern="%",
        scheduled_for=_now(),
        notes=f"created via quick-deploy ({severity.value if severity else vendor})",
    )
    win.auto_execute = True

    # Pre-pick all candidate packages so the deploy is truly one-click — no
    # second-screen package selection. If filtering by vendor (not severity),
    # narrow the package list to matching publishers; severity_filter is
    # already enforced by window_candidate_packages.
    candidates = window_candidate_packages(db, win)
    if vendor:
        v_l = vendor.lower()
        candidates = [c for c in candidates if (c.get("vendor") or "").lower() == v_l]
    win.selected_packages = [c["name"] for c in candidates] if candidates else None

    db.commit()
    db.refresh(win)

    # Flip straight to in_progress so the next agent check-in picks it up.
    transition_window(db, window=win, new_status=PatchWindowStatus.in_progress)
    return win


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


# ---------------------------------------------------------- vendor / OEM grouping

# Canonical vendor names — first segment of a winget package Id maps here.
_VENDOR_CANON = {
    "microsoft":          "Microsoft",
    "google":             "Google",
    "googlellc":          "Google",
    "mozilla":            "Mozilla",
    "adobe":              "Adobe",
    "oracle":             "Oracle",
    "apple":              "Apple",
    "slacktechnologies":  "Slack",
    "slack":              "Slack",
    "zoom":               "Zoom",
    "notion":             "Notion",
    "atlassian":          "Atlassian",
    "jetbrains":          "JetBrains",
    "git":                "Git for Windows",
    "github":             "GitHub",
    "docker":             "Docker",
    "videolan":           "VideoLAN (VLC)",
    "7zip":               "7-Zip",
    "winrar":             "WinRAR",
    "putty":              "PuTTY",
    "wireshark":          "Wireshark",
    "tightvnc":           "TightVNC",
    "teamviewer":         "TeamViewer",
    "cisco":              "Cisco",
    "vmware":             "VMware",
    "intel":              "Intel",
    "amd":                "AMD",
    "nvidia":             "NVIDIA",
    "lenovo":             "Lenovo",
    "dell":               "Dell",
    "hp":                 "HP",
    "kaspersky":          "Kaspersky",
    "bitdefender":        "Bitdefender",
    "symantec":           "Symantec",
}


def derive_vendor(package_name: str, source: str | None = None) -> str:
    """Best-effort vendor name from a package id + source.

    Winget package ids follow Vendor.Product.SubProduct → vendor = first segment.
    Windows Update KBs (KB#####) → Microsoft Windows Update.
    Linux apt packages → Linux (since this product is Windows-only going forward,
    only the demo droplet hits this branch).
    Anything unrecognisable → "Other".
    """
    pn = (package_name or "").strip()
    if not pn:
        return "Other"
    # Windows Update KB
    if pn.upper().startswith("KB") and pn[2:].isdigit():
        return "Microsoft Windows Update"
    # Source-based hints
    s = (source or "").lower()
    if s.startswith("apt") or s.startswith("dpkg") or s == "rpm":
        return "Linux distro packages"
    if s == "windows-update":
        return "Microsoft Windows Update"
    # Winget Vendor.Product
    if "." in pn:
        first = pn.split(".", 1)[0].lower()
        return _VENDOR_CANON.get(first, first.capitalize() if first else "Other")
    # Single-word: try to canonicalise too
    canon = _VENDOR_CANON.get(pn.lower())
    return canon or "Other"


def window_candidate_packages(db: Session, window: PatchWindow) -> list[dict]:
    """Distinct missing packages across this window's targets.

    Each row: {name, vendor, severity, count, sources, hostnames_sample, selected}.
    Sorted by vendor, then severity (critical first), then count descending.
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
            "vendor": derive_vendor(d["name"], next(iter(d["sources"])) if d["sources"] else None),
            "severity": d["severity"],
            "count": d["count"],
            "sources": ", ".join(sorted(d["sources"])),
            "hostnames_sample": ", ".join(d["hostnames"][:5]) + ("…" if len(d["hostnames"]) > 5 else ""),
            "title": d["title"] or "",
            "selected": d["name"] in selected_set,
        })
    sev_rank = {"critical": 0, "important": 1, "moderate": 2, "low": 3, "unknown": 4}
    out.sort(key=lambda r: (r["vendor"], sev_rank.get(r["severity"], 9), -r["count"], r["name"]))
    return out


def fleet_vendor_breakdown(db: Session, tenant_id: int) -> list[dict]:
    """All currently-missing patches across the fleet, grouped by vendor/OEM.

    Each row: {vendor, total, critical, important, moderate, low_unknown, packages}
    where packages is a count of distinct package names from that vendor.
    """
    rows = (db.query(PatchObservation)
              .join(Agent, Agent.id == PatchObservation.agent_id)
              .filter(Agent.tenant_id == tenant_id,
                      PatchObservation.resolved_at.is_(None))
              .all())

    by_vendor: dict[str, dict] = {}
    for o in rows:
        v = derive_vendor(o.package_name, o.source)
        d = by_vendor.setdefault(v, {
            "vendor": v, "total": 0,
            "critical": 0, "important": 0, "moderate": 0, "low_unknown": 0,
            "_packages": set(),
        })
        d["total"] += 1
        d["_packages"].add(o.package_name)
        sev = o.severity.value
        if sev == "critical":     d["critical"] += 1
        elif sev == "important":  d["important"] += 1
        elif sev == "moderate":   d["moderate"] += 1
        else:                     d["low_unknown"] += 1

    out = []
    for d in by_vendor.values():
        d["packages"] = len(d.pop("_packages"))
        out.append(d)
    out.sort(key=lambda d: (-d["critical"], -d["total"], d["vendor"]))
    return out
