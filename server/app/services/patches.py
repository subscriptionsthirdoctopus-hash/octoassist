"""Patch Management — fleet view, aging history, deployment windows."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from sqlalchemy import and_, or_, not_

from ..models import (
    Agent,
    AssetSnapshot,
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


# ---------------------------------------------------------------------------
# Windows-only filter
# ---------------------------------------------------------------------------
# We're going Windows-only on patch management. Linux endpoints + apt/dnf
# sources are excluded from every aggregate. The detection is per-observation
# (the `source` column is "windows-update" / "winget" for Windows and
# "apt:*" / "dnf:*" for Linux), and per-agent (latest snapshot's os.caption).

_LINUX_SOURCE_PREFIXES = ("apt:", "dnf:", "yum:", "zypper:")


def _windows_source_filter():
    """SQLAlchemy filter to restrict patch_observations to Windows sources."""
    return not_(or_(*[PatchObservation.source.like(p + "%") for p in _LINUX_SOURCE_PREFIXES]))


def is_windows_agent(latest_payload: dict | None) -> bool:
    """Inspect the latest snapshot to decide if an agent is a Windows host."""
    if not latest_payload:
        return False
    cap = ((latest_payload.get("os") or {}).get("caption") or "").lower()
    return "windows" in cap or "microsoft" in cap


# ---------------------------------------------------------- "currently missing"

def fleet_patch_summary(db: Session, tenant_id: int) -> list[dict]:
    """Per-endpoint patch posture (Windows-only, currently-missing). Sorted worst-first."""
    rows = (db.query(
                Agent.id, Agent.hostname, Agent.last_seen_at,
                func.count(PatchObservation.id).label("total"),
                func.sum(case((PatchObservation.severity == PatchSeverity.critical, 1), else_=0)).label("critical"),
                func.sum(case((PatchObservation.severity == PatchSeverity.important, 1), else_=0)).label("important"),
                func.sum(case((PatchObservation.severity == PatchSeverity.moderate, 1), else_=0)).label("moderate"),
                func.min(PatchObservation.first_seen_at).label("oldest_seen"),
            )
            .outerjoin(PatchObservation,
                       and_(PatchObservation.agent_id == Agent.id,
                            PatchObservation.resolved_at.is_(None),
                            _windows_source_filter()))
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
                      PatchObservation.resolved_at.is_(None),
                      _windows_source_filter())
              .group_by(PatchObservation.severity)
              .all())
    order = {"critical": 0, "important": 1, "moderate": 2, "low": 3, "unknown": 4}
    return sorted([(s.value, int(c)) for s, c in rows], key=lambda x: order.get(x[0], 9))


def top_missing_packages(db: Session, tenant_id: int, *, top: int = 20) -> list[tuple[str, int]]:
    rows = (db.query(PatchObservation.package_name, func.count(PatchObservation.id))
              .join(Agent, Agent.id == PatchObservation.agent_id)
              .filter(Agent.tenant_id == tenant_id,
                      PatchObservation.resolved_at.is_(None),
                      _windows_source_filter())
              .group_by(PatchObservation.package_name)
              .order_by(func.count(PatchObservation.id).desc())
              .limit(top)
              .all())
    return [(r[0], int(r[1])) for r in rows]


def windows_endpoint_dashboard(db: Session, tenant_id: int) -> list[dict]:
    """Per-Windows-endpoint patch dashboard.

    Returns one row per Windows agent (Linux/Mac filtered out via the latest
    snapshot's os.caption). Each row carries:
        hostname, agent_id, os_caption, last_seen_at, last_scan_at,
        psw_installed, winget_available,
        pending_count, critical / important / moderate counts,
        fully_updated (bool), scan_failed (bool),
        compliance_pct  — 100 if pending=0 else (1 - pending/baseline) ranged
    Sorted: scan-failed first, then by pending count desc, then hostname.
    """
    # Pull the LATEST snapshot per agent so we can read os caption + patch_scan.
    sub = (db.query(AssetSnapshot.agent_id,
                    func.max(AssetSnapshot.snapshot_at).label("latest"))
             .group_by(AssetSnapshot.agent_id).subquery())
    snaps = (db.query(Agent, AssetSnapshot.payload)
               .join(AssetSnapshot, AssetSnapshot.agent_id == Agent.id)
               .join(sub, and_(AssetSnapshot.agent_id == sub.c.agent_id,
                               AssetSnapshot.snapshot_at == sub.c.latest))
               .filter(Agent.tenant_id == tenant_id)
               .all())

    # Per-agent windows-only patch counts.
    counts_q = (db.query(
                    PatchObservation.agent_id,
                    PatchObservation.severity,
                    func.count(PatchObservation.id))
                  .join(Agent, Agent.id == PatchObservation.agent_id)
                  .filter(Agent.tenant_id == tenant_id,
                          PatchObservation.resolved_at.is_(None),
                          _windows_source_filter())
                  .group_by(PatchObservation.agent_id, PatchObservation.severity)
                  .all())
    counts: dict[int, dict[str, int]] = {}
    for aid, sev, n in counts_q:
        counts.setdefault(int(aid), {})[sev.value] = int(n)

    out: list[dict] = []
    for agent, payload in snaps:
        if not is_windows_agent(payload):
            continue
        scan = (payload or {}).get("patch_scan") or {}
        c    = counts.get(agent.id, {})
        crit = c.get("critical", 0)
        imp  = c.get("important", 0)
        mod  = c.get("moderate", 0)
        low  = c.get("low", 0)
        unk  = c.get("unknown", 0)
        pending = crit + imp + mod + low + unk

        scan_success = bool(scan.get("scan_success"))
        # If the agent's payload doesn't have patch_scan yet (old agents),
        # fall back to "scan succeeded if we have anything from it OR the
        # post-install zero state". For UI purposes we treat missing scan
        # data as 'scan failed' so the admin knows to refresh that agent.
        scan_known = "patch_scan" in (payload or {})

        # Compliance % — 100 when nothing pending, otherwise a soft signal
        # weighted by severity (critical hurts more than moderate).
        if not scan_known:
            compliance = None
        elif pending == 0:
            compliance = 100
        else:
            weight = crit * 4 + imp * 2 + mod * 1 + low * 0.5 + unk * 0.5
            compliance = max(0, int(round(100 - min(100, weight * 5))))

        # Online classification: with the 30-sec poll, an agent that hasn't
        # called in for >2 min is suspect; >5 min is "offline" (powered off,
        # no network, daemon stopped). Drives the green/amber/red dot.
        online = "offline"
        if agent.last_seen_at is not None:
            age = (_now() - agent.last_seen_at).total_seconds()
            if age <= 120:    online = "online"      # ✓ within 2 polls
            elif age <= 300:  online = "lagging"    # ⚠ recent but stale
        out.append({
            "agent_id":          agent.id,
            "hostname":          agent.hostname,
            "os_caption":        (payload or {}).get("os", {}).get("caption", ""),
            "last_seen_at":      agent.last_seen_at,
            "last_scan_at":      scan.get("scanned_at"),
            "psw_installed":     bool(scan.get("psw_installed")),
            "winget_available":  bool(scan.get("winget_available")),
            "scan_success":      scan_success,
            "scan_known":        scan_known,
            "pending_count":     pending,
            "critical":          crit,
            "important":         imp,
            "moderate":          mod,
            "low":               low,
            "unknown":           unk,
            "fully_updated":     scan_success and scan_known and pending == 0,
            "scan_failed":       scan_known and not scan_success,
            "needs_refresh":     not scan_known,  # old-agent flag
            "compliance_pct":    compliance,
            "online":            online,
        })

    # Sort: needs_refresh / scan_failed first, then by pending desc, then host.
    out.sort(key=lambda r: (
        not r["needs_refresh"], not r["scan_failed"],
        -r["pending_count"], r["hostname"].lower(),
    ))
    return out


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
    """KPI tiles for the /patches page. Windows-only — Linux endpoints and
    apt/dnf-sourced patches are excluded everywhere."""
    # Count of Windows endpoints only (from latest snapshot OS caption)
    dashboard = windows_endpoint_dashboard(db, tenant_id)
    total_endpoints = len(dashboard)
    fully = sum(1 for r in dashboard if r["fully_updated"])
    non_compliant = sum(1 for r in dashboard if r["pending_count"] > 0)
    needs_refresh = sum(1 for r in dashboard if r["needs_refresh"])
    pct = round(100 * fully / total_endpoints) if total_endpoints else 100

    total_critical = (db.query(func.count(PatchObservation.id))
                        .join(Agent, Agent.id == PatchObservation.agent_id)
                        .filter(Agent.tenant_id == tenant_id,
                                PatchObservation.resolved_at.is_(None),
                                PatchObservation.severity == PatchSeverity.critical,
                                _windows_source_filter())
                        .scalar()) or 0

    total_patches = (db.query(func.count(PatchObservation.id))
                       .join(Agent, Agent.id == PatchObservation.agent_id)
                       .filter(Agent.tenant_id == tenant_id,
                               PatchObservation.resolved_at.is_(None),
                               _windows_source_filter())
                       .scalar()) or 0

    return {
        "total_endpoints":     int(total_endpoints),
        "compliant_endpoints": int(fully),
        "non_compliant":       int(non_compliant),
        "needs_refresh":       int(needs_refresh),
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
        # Flip any live targets (planned / in_progress) to skipped so the
        # window can be cleanly deleted later and the agent doesn't keep
        # any stale work in its view. The agent already short-circuits any
        # work for a cancelled-window target on its next poll, but updating
        # the row makes the DB self-consistent immediately.
        from ..models import PatchWindowTargetStatus
        for t in window.targets:
            if t.status in (PatchWindowTargetStatus.planned,
                            PatchWindowTargetStatus.in_progress):
                t.status = PatchWindowTargetStatus.skipped
                t.note = ((t.note or "") + " · window cancelled").strip(" ·")[:1000]
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
    all_severities: bool = False,
    scheduled_for: datetime | None = None,
) -> PatchWindow:
    """Intune / Zoho Endpoint Central-style one-click deploy.

    Creates a patch window in `in_progress` with auto_execute=True and
    auto-selects all matching candidate packages, so the agent picks the
    job up on its next check-in (no further admin action required).

    Exactly one of (severity, vendor, all_severities) must indicate intent.
    """
    from datetime import timezone as _tz, timedelta as _td

    if severity is None and not vendor and not all_severities:
        raise ValueError("quick_deploy_category requires severity, vendor, or all_severities")

    IST = _tz(_td(hours=5, minutes=30), name="IST")
    now_ist = _now().astimezone(IST).strftime("%Y-%m-%d %H:%M IST")

    if all_severities:
        name = f"Quick deploy — All pending patches — {now_ist}"
        desc = ("Auto-created one-click deployment for ALL currently-missing "
                "patches across the fleet, regardless of severity. "
                "Started immediately, auto-executes on next agent check-in.")
    elif severity is not None:
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

    is_scheduled = scheduled_for is not None and scheduled_for > _now()
    if is_scheduled:
        ist_str = scheduled_for.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")
        name = name.replace(now_ist, f"runs at {ist_str}")
        desc = desc.replace(
            "Started immediately, auto-executes on next agent check-in.",
            f"Auto-promotes to in_progress at {ist_str}; agents pick it up on the next 5-min poll after that.",
        )

    win = create_window(
        db,
        tenant_id=tenant_id, creator=creator,
        name=name, description=desc,
        severity_filter=severity,
        hostname_pattern="%",
        scheduled_for=scheduled_for or _now(),
        notes=f"created via quick-deploy ({severity.value if severity else (vendor or 'all')})",
    )
    win.auto_execute = True

    # Pre-pick all candidate packages so the deploy is truly one-click — no
    # second-screen package selection. CRITICAL: Quick-deploy is Windows-only —
    # filter out any apt:/dnf:/yum:/zypper: sources so we never push Linux
    # packages to Windows endpoints (or vice-versa). The dashboard already
    # filters at read-time, but the deploy path also needs to filter at write
    # so the window's selected_packages doesn't contain Linux names.
    candidates = window_candidate_packages(db, win)
    candidates = [c for c in candidates
                  if not any((c.get("sources") or "").startswith(p)
                             for p in _LINUX_SOURCE_PREFIXES)]
    if vendor:
        v_l = vendor.lower()
        candidates = [c for c in candidates if (c.get("vendor") or "").lower() == v_l]
    win.selected_packages = [c["name"] for c in candidates] if candidates else None

    if is_scheduled:
        # Park in 'scheduled' state — background scheduler promotes to
        # in_progress when scheduled_for arrives.
        win.status = PatchWindowStatus.scheduled
        db.commit()
        db.refresh(win)
        return win

    db.commit()
    db.refresh(win)
    # Flip straight to in_progress so the next agent check-in picks it up.
    transition_window(db, window=win, new_status=PatchWindowStatus.in_progress)
    return win


def create_bulk_window(
    db: Session, *,
    tenant_id: int,
    creator: User,
    name: str | None,
    agent_ids: list[int],
    package_names: list[str],
    scheduled_for: datetime | None = None,
) -> PatchWindow:
    """Phase E bulk deploy: explicit list of endpoints + explicit list of
    packages, no hostname pattern. Creates a window with hand-picked targets
    and selected_packages, auto-executes (or schedules if scheduled_for is
    in the future).
    """
    from datetime import timezone as _tz, timedelta as _td

    if not agent_ids:
        raise ValueError("create_bulk_window: at least one agent_id required")
    if not package_names:
        raise ValueError("create_bulk_window: at least one package required")

    IST = _tz(_td(hours=5, minutes=30), name="IST")
    now_ist = _now().astimezone(IST).strftime("%Y-%m-%d %H:%M IST")
    label = (name or "").strip() or f"Bulk deploy — {len(agent_ids)} endpoint(s) × {len(package_names)} package(s) — {now_ist}"
    desc = (f"Bulk deploy — hand-picked {len(agent_ids)} endpoint(s) "
            f"and {len(package_names)} package(s). Auto-executes on next agent check-in.")

    is_scheduled = scheduled_for is not None and scheduled_for > _now()

    win = PatchWindow(
        tenant_id=tenant_id,
        name=label[:160],
        description=desc,
        severity_filter=None,
        hostname_pattern="(bulk)",
        scheduled_for=scheduled_for or _now(),
        notes="created via bulk-deploy",
        created_by_id=creator.id,
        status=PatchWindowStatus.draft,
        auto_execute=True,
        selected_packages=package_names,
    )
    db.add(win)
    db.flush()

    # Materialise targets ONLY for the selected agent_ids — bypassing pattern.
    counts: dict[int, int] = {}
    for aid, c in (db.query(PatchObservation.agent_id, func.count(PatchObservation.id))
                     .join(Agent, Agent.id == PatchObservation.agent_id)
                     .filter(Agent.tenant_id == tenant_id,
                             Agent.id.in_(agent_ids),
                             PatchObservation.resolved_at.is_(None))
                     .group_by(PatchObservation.agent_id).all()):
        counts[int(aid)] = int(c)
    for aid in agent_ids:
        db.add(PatchWindowTarget(
            window_id=win.id,
            agent_id=aid,
            status=PatchWindowTargetStatus.planned,
            missing_at_plan=counts.get(aid, 0),
        ))

    db.commit()
    db.refresh(win)

    if not is_scheduled:
        transition_window(db, window=win, new_status=PatchWindowStatus.in_progress)
    else:
        win.status = PatchWindowStatus.scheduled
        db.commit()
        db.refresh(win)
    return win


def deploy_to_single_endpoint(
    db: Session, *,
    tenant_id: int,
    creator: User,
    agent_id: int,
    package_names: list[str],
    scheduled_for: datetime | None = None,
) -> PatchWindow:
    """Per-endpoint hand-pick deploy. Creates an in_progress window (or a
    scheduled one if scheduled_for is in the future), scoped to one agent
    hostname pattern, with auto_execute=True and the admin's chosen package
    list pre-selected. Agent picks up on next check-in.
    """
    from datetime import timezone as _tz, timedelta as _td
    agent = db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != tenant_id:
        raise ValueError("agent not found in this tenant")
    if not package_names:
        raise ValueError("no packages selected")

    IST = _tz(_td(hours=5, minutes=30), name="IST")
    is_scheduled = scheduled_for is not None and scheduled_for > _now()
    when_label = (scheduled_for.astimezone(IST).strftime("runs at %Y-%m-%d %H:%M IST")
                  if is_scheduled
                  else _now().astimezone(IST).strftime("%Y-%m-%d %H:%M IST"))
    name = f"Deploy to {agent.hostname} — {len(package_names)} patches — {when_label}"
    desc = (f"Hand-picked deployment of {len(package_names)} patches to "
            f"{agent.hostname}. "
            + ("Auto-promotes at the scheduled time; agents pick it up on the next 5-min poll after that."
               if is_scheduled else "Auto-executes on next agent check-in."))

    win = create_window(
        db,
        tenant_id=tenant_id, creator=creator,
        name=name, description=desc,
        severity_filter=None,
        hostname_pattern=agent.hostname,  # exact-match LIKE
        scheduled_for=scheduled_for or _now(),
        notes=f"hand-pick from /patches/{agent_id}",
    )
    win.auto_execute = True
    # Sanitise package list against this agent's actual missing patches
    valid = {n for (n,) in db.query(PatchObservation.package_name)
                             .filter(PatchObservation.agent_id == agent_id,
                                     PatchObservation.resolved_at.is_(None))
                             .all()}
    chosen = sorted({p for p in package_names if p in valid})
    win.selected_packages = chosen if chosen else None
    if is_scheduled:
        win.status = PatchWindowStatus.scheduled
        db.commit()
        db.refresh(win)
        return win
    db.commit()
    db.refresh(win)
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
                      PatchObservation.resolved_at.is_(None),
                      _windows_source_filter())
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
