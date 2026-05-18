"""Microsoft Graph device sync — pulls Windows endpoints from Entra ID into
OctoAssist's EntraDevice table.

Uses the same OAuth 2.0 client_credentials flow + app registration as the
user sync. Requires the additional Graph application permission
`Device.Read.All` with admin consent granted.

Filter: only `operatingSystem eq 'Windows'`. This excludes phones (iOS,
Android), Macs (MacMDM), iPads — anyone in your tenant who'd never be a
candidate for OctoAssist regardless. Windows desktops + Windows laptops
both pass through (Entra doesn't reliably distinguish form factor).

Strategy:
- Match existing EntraDevice rows by `entra_device_id` (immutable GUID).
- Try to link to a User by reading `/devices/{id}/registeredOwners`. Owners
  who haven't been synced into the users table yet are silently ignored —
  they'll link on the next user sync.
- Never overwrite an existing OctoAssist Agent row; EntraDevice is purely
  a discovered-but-unmanaged register. The /assets view merges them.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from ..models import EntraDevice, User
from .sso import EntraConfig
from .entra_directory import _get_app_token, EntraDirectoryError

log = logging.getLogger("octoassist.entra_devices")


@dataclass
class DeviceSyncReport:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    linked_users: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []

    def summary(self) -> str:
        bits = [f"fetched={self.fetched}", f"created={self.created}",
                f"updated={self.updated}", f"skipped={self.skipped}",
                f"users_linked={self.linked_users}"]
        if self.errors:
            bits.append(f"errors={len(self.errors)}")
        return ", ".join(bits)


async def fetch_windows_devices(cfg: EntraConfig) -> list[dict[str, Any]]:
    """Pull every Windows device from the tenant. Paginated via @odata.nextLink."""
    token = await _get_app_token(cfg)
    headers = {"Authorization": f"Bearer {token}",
               "ConsistencyLevel": "eventual"}
    base = ("https://graph.microsoft.com/v1.0/devices"
            "?$filter=operatingSystem eq 'Windows'"
            "&$select=id,displayName,operatingSystem,operatingSystemVersion,"
            "manufacturer,model,isCompliant,isManaged,accountEnabled,"
            "approximateLastSignInDateTime"
            "&$top=999")
    out: list[dict] = []
    next_url: str | None = base
    async with httpx.AsyncClient(timeout=30) as c:
        while next_url:
            r = await c.get(next_url, headers=headers)
            if r.status_code != 200:
                raise EntraDirectoryError(f"devices list: {r.status_code} {r.text[:400]}")
            payload = r.json()
            out.extend(payload.get("value") or [])
            next_url = payload.get("@odata.nextLink")
    return out


async def fetch_device_owner_oid(cfg: EntraConfig, device_id: str) -> str | None:
    """Return the entra_oid of the device's first registered owner, or None.

    Graph: /devices/{id}/registeredOwners returns a list of directoryObjects.
    Empty for unowned / shared devices.
    """
    token = await _get_app_token(cfg)
    headers = {"Authorization": f"Bearer {token}"}
    url = (f"https://graph.microsoft.com/v1.0/devices/{device_id}/registeredOwners"
           f"?$select=id&$top=1")
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.get(url, headers=headers)
            if r.status_code != 200:
                return None
            values = r.json().get("value") or []
            if not values:
                return None
            return (values[0].get("id") or None)
        except Exception:  # noqa: BLE001
            return None


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # Graph returns RFC 3339 ISO 8601. Strip trailing Z if present.
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def upsert_device(
    db: Session, *, tenant_id: int, graph_device: dict[str, Any],
    owner_oid: str | None,
) -> tuple[EntraDevice | None, str]:
    """Insert or update one EntraDevice from a Graph device record.

    Returns (device, status) where status is 'created' | 'updated' | 'skipped'.
    """
    eid = (graph_device.get("id") or "").strip()
    name = (graph_device.get("displayName") or "").strip()
    if not eid or not name:
        return None, "skipped"

    existing = db.query(EntraDevice).filter(EntraDevice.entra_device_id == eid).first()
    now = datetime.now(timezone.utc)

    primary_user_id = None
    if owner_oid:
        owner = db.query(User).filter(User.entra_oid == owner_oid).first()
        if owner:
            primary_user_id = owner.id

    fields = dict(
        tenant_id=tenant_id,
        display_name=name[:255],
        operating_system=(graph_device.get("operatingSystem") or "")[:60] or None,
        os_version=(graph_device.get("operatingSystemVersion") or "")[:60] or None,
        manufacturer=(graph_device.get("manufacturer") or "")[:120] or None,
        model=(graph_device.get("model") or "")[:120] or None,
        is_compliant=graph_device.get("isCompliant"),
        is_managed=graph_device.get("isManaged"),
        account_enabled=graph_device.get("accountEnabled"),
        approx_last_signin_at=_parse_dt(graph_device.get("approximateLastSignInDateTime")),
        primary_user_id=primary_user_id,
        synced_at=now,
    )

    if existing is None:
        d = EntraDevice(entra_device_id=eid, **fields)
        db.add(d)
        return d, "created"

    for k, v in fields.items():
        setattr(existing, k, v)
    return existing, "updated"


async def sync_devices(
    db: Session, *, tenant_id: int, cfg: EntraConfig,
) -> DeviceSyncReport:
    """End-to-end: pull Windows devices from Graph, upsert each, link owners,
    commit, return a report."""
    report = DeviceSyncReport()
    try:
        devices = await fetch_windows_devices(cfg)
    except EntraDirectoryError as e:
        report.errors.append(str(e))
        return report

    report.fetched = len(devices)
    for d in devices:
        try:
            owner_oid = await fetch_device_owner_oid(cfg, d.get("id") or "")
            _, status = upsert_device(
                db, tenant_id=tenant_id, graph_device=d, owner_oid=owner_oid,
            )
            if status == "created":
                report.created += 1
            elif status == "updated":
                report.updated += 1
            else:
                report.skipped += 1
            if owner_oid:
                report.linked_users += 1
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"{d.get('displayName','?')}: {e}")
    db.commit()
    log.info("entra device sync done: %s", report.summary())
    return report
