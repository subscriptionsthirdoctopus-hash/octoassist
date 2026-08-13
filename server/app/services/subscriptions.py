"""Software Subscriptions — owned licences, seats, POs and expiry.

Answers "what have we bought?", where services/sam.py answers "what is
installed?". Reconciling the two is the point of a SAM audit, so they are kept
as separate records rather than one guessing at the other.

Also seeds Windows OEM keys from endpoint snapshots — the agent already reads
OA3xOriginalProductKey out of firmware, so the keys for most machines can be
imported rather than re-typed.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Agent, AssetSnapshot, SoftwareSubscription

# How far ahead the expiry digest looks. 30 days is one purchase-approval cycle
# for most customers — long enough to renew without an emergency.
DEFAULT_HORIZON_DAYS = 30


def _today() -> date:
    # IST is where every current customer operates; a digest sent at 23:30 IST
    # must not report "tomorrow" because UTC has already rolled over.
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).date()


def list_subscriptions(db: Session, tenant_id: int, q: str = "") -> list[SoftwareSubscription]:
    query = (db.query(SoftwareSubscription)
               .filter(SoftwareSubscription.tenant_id == tenant_id))
    needle = (q or "").strip().lower()
    if needle:
        like = f"%{needle}%"
        query = query.filter(
            func.lower(SoftwareSubscription.software_name).like(like)
            | func.lower(func.coalesce(SoftwareSubscription.vendor, "")).like(like)
            | func.lower(func.coalesce(SoftwareSubscription.po_reference, "")).like(like)
        )
    # Soonest expiry first; perpetual licences (NULL) sort to the end.
    return query.order_by(
        SoftwareSubscription.expires_on.is_(None),
        SoftwareSubscription.expires_on.asc(),
        SoftwareSubscription.software_name.asc(),
    ).all()


def days_to_expiry(sub: SoftwareSubscription, today: date | None = None) -> int | None:
    """Days until expiry. Negative if already lapsed, None if perpetual."""
    if sub.expires_on is None:
        return None
    return (sub.expires_on - (today or _today())).days


def status_of(sub: SoftwareSubscription, today: date | None = None,
              horizon_days: int = DEFAULT_HORIZON_DAYS) -> str:
    """One of: perpetual | expired | expiring | active."""
    days = days_to_expiry(sub, today)
    if days is None:
        return "perpetual"
    if days < 0:
        return "expired"
    if days <= horizon_days:
        return "expiring"
    return "active"


def _attention_query(db: Session, tenant_id: int,
                     horizon_days: int = DEFAULT_HORIZON_DAYS,
                     include_expired: bool = True):
    """The one definition of "needs attention within the horizon".

    Every count and every list of expiring software must come from here. A card
    that counts one predicate while its drilldown lists another is the bug TEMA
    reported on 07 Aug: the widget read 0 while opening a populated list,
    because the count excluded already-lapsed rows and the list included them.
    Sharing the query makes that class of mismatch unrepresentable.
    """
    today = _today()
    cutoff = today + timedelta(days=horizon_days)
    query = (db.query(SoftwareSubscription)
               .filter(SoftwareSubscription.tenant_id == tenant_id,
                       SoftwareSubscription.expires_on.isnot(None),
                       SoftwareSubscription.expires_on <= cutoff))
    if not include_expired:
        query = query.filter(SoftwareSubscription.expires_on >= today)
    return query


def expiring_soon(db: Session, tenant_id: int,
                  horizon_days: int = DEFAULT_HORIZON_DAYS,
                  include_expired: bool = True) -> list[SoftwareSubscription]:
    """Subscriptions needing attention, soonest first.

    Perpetual licences are excluded outright — they have no expiry to chase.
    Already-lapsed ones are included by default: dropping them would silently
    hide the most urgent rows once they tip past zero.
    """
    return (_attention_query(db, tenant_id, horizon_days, include_expired)
            .order_by(SoftwareSubscription.expires_on.asc(),
                      SoftwareSubscription.software_name.asc()).all())


def expiring_soon_count(db: Session, tenant_id: int,
                        horizon_days: int = DEFAULT_HORIZON_DAYS,
                        include_expired: bool = True) -> int:
    """How many rows expiring_soon() would return, counted in the database.

    Use this for any headline number whose drilldown is expiring_soon(), so the
    two cannot drift apart.
    """
    return _attention_query(db, tenant_id, horizon_days, include_expired).count()


def kpis(db: Session, tenant_id: int, horizon_days: int = DEFAULT_HORIZON_DAYS) -> dict:
    rows = list_subscriptions(db, tenant_id)
    today = _today()
    counts = {"total": len(rows), "expired": 0, "expiring": 0, "active": 0, "perpetual": 0}
    seats = 0
    for r in rows:
        counts[status_of(r, today, horizon_days)] += 1
        seats += r.seats or 0
    counts["seats"] = seats
    # "expiring" is strictly the not-yet-lapsed window, which is only meaningful
    # next to "expired". `attention` is the pair together — the number that
    # matches what expiring_soon() lists, and the only one safe to put on a
    # card that opens that list.
    counts["attention"] = counts["expired"] + counts["expiring"]
    return counts


# ---------------------------------------------------------------------------
# Seeding Windows keys from what the agents already report
# ---------------------------------------------------------------------------

def _latest_snapshot_rows(db: Session, tenant_id: int):
    sub = (db.query(AssetSnapshot.agent_id,
                    func.max(AssetSnapshot.snapshot_at).label("latest"))
             .group_by(AssetSnapshot.agent_id).subquery())
    return (db.query(Agent, AssetSnapshot.payload)
              .join(AssetSnapshot, AssetSnapshot.agent_id == Agent.id)
              .join(sub, (AssetSnapshot.agent_id == sub.c.agent_id) &
                         (AssetSnapshot.snapshot_at == sub.c.latest))
              .filter(Agent.tenant_id == tenant_id)
              .all())


def discover_windows_keys(db: Session, tenant_id: int) -> list[dict]:
    """Windows product keys visible in the latest snapshot of each endpoint.

    Only the OEM/firmware key is recoverable this way (OA3xOriginalProductKey).
    Retail and volume-licensed machines report no key, and are returned with
    key=None so the UI can show what still needs entering by hand rather than
    quietly omitting those endpoints.
    """
    out = []
    for agent, payload in _latest_snapshot_rows(db, tenant_id):
        os_info = (payload or {}).get("os") or {}
        caption = (os_info.get("caption") or "").strip()
        if caption and "windows" not in caption.lower():
            continue
        out.append({
            "agent_id":   agent.id,
            "hostname":   agent.hostname,
            "edition":    caption or "Windows",
            "key":        (os_info.get("product_key") or "").strip() or None,
            "activation": (os_info.get("activation_status") or "").strip() or None,
        })
    out.sort(key=lambda r: r["hostname"].lower())
    return out


def import_windows_keys(db: Session, tenant_id: int, created_by_id: int | None = None) -> dict:
    """Create a subscription row per endpoint that reports a Windows OEM key.

    Idempotent on (agent_id, license_key): re-running after new endpoints
    enrol adds only the new ones, so this is safe to use as a refresh. Rows
    edited by hand are never overwritten.

    Returns {"created": n, "skipped_existing": n, "no_key": n}.
    """
    discovered = discover_windows_keys(db, tenant_id)
    existing = {
        (s.agent_id, (s.license_key or "").strip())
        for s in db.query(SoftwareSubscription)
                   .filter(SoftwareSubscription.tenant_id == tenant_id).all()
    }
    created = skipped = no_key = 0
    for row in discovered:
        if not row["key"]:
            no_key += 1
            continue
        if (row["agent_id"], row["key"]) in existing:
            skipped += 1
            continue
        db.add(SoftwareSubscription(
            tenant_id=tenant_id,
            software_name=row["edition"],
            vendor="Microsoft",
            license_key=row["key"],
            seats=1,
            agent_id=row["agent_id"],
            # OEM Windows is perpetual and tied to the board — no expiry.
            expires_on=None,
            notes=(f"Imported from endpoint {row['hostname']} "
                   f"(OEM firmware key; activation: {row['activation'] or 'unknown'})."),
            created_by_id=created_by_id,
        ))
        existing.add((row["agent_id"], row["key"]))
        created += 1
    db.commit()
    return {"created": created, "skipped_existing": skipped, "no_key": no_key}


def export_csv(db: Session, tenant_id: int) -> str:
    rows = list_subscriptions(db, tenant_id)
    today = _today()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["software_name", "vendor", "license_key", "seats", "po_reference",
                "purchased_on", "starts_on", "expires_on", "days_to_expiry",
                "status", "endpoint", "notes"])
    for s in rows:
        w.writerow([
            s.software_name, s.vendor or "", s.license_key or "", s.seats or "",
            s.po_reference or "",
            s.purchased_on.isoformat() if s.purchased_on else "",
            s.starts_on.isoformat() if s.starts_on else "",
            s.expires_on.isoformat() if s.expires_on else "",
            days_to_expiry(s, today) if s.expires_on else "",
            status_of(s, today),
            s.agent.hostname if s.agent else "",
            (s.notes or "").replace("\n", " "),
        ])
    return buf.getvalue()
