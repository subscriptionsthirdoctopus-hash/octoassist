import re
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from ..jinja_filters import IST, install_on
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import current_user, require_admin, require_staff
from ..database import get_db
from ..models import (
    Agent, AssetSnapshot, EntraDevice, IdentityProvider, IdentityProviderKind,
    Tenant, User,
)
from ..services import entra_devices
from ..services.hostnames import normalise as normalise_hostname
from ..services import compliance
from ..services import csv_export, natural_sort, paging
from ..services.sso import parse_entra_config

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(templates)

router = APIRouter(tags=["web"])


def _ctx(user: User, db: Session, **extra) -> dict:
    tenant = db.query(Tenant).first()
    return {"current_user": user, "tenant": tenant, **extra}


def _latest_snapshot(db: Session, agent_id: int) -> AssetSnapshot | None:
    return (
        db.query(AssetSnapshot)
        .filter(AssetSnapshot.agent_id == agent_id)
        .order_by(AssetSnapshot.snapshot_at.desc())
        .first()
    )


def _fold_variants(values) -> list[str]:
    """Collapse spellings that differ only by case or padding into one entry.

    The filter compares dimensions trimmed and lowercased, so "Achhad",
    "achhad " and "Achhad " all select the same endpoints. Offering three
    dropdown entries that each return the identical rows reads as a broken
    filter, and -- worse for anyone reconciling a count -- invites the reader
    to assume the site's assets are split across them.

    One entry is shown per distinct place, spelled the way it is most likely to
    have been meant: a capitalised variant wins over an all-lowercase one, and
    ties are broken alphabetically so the list is stable between requests.

    This is presentation only. The underlying records keep their own spelling,
    because collapsing them for real is a data correction that belongs to the
    customer -- and the routing rules read those same columns.
    """
    by_key: dict[str, list[str]] = {}
    for raw in values:
        cleaned = (raw or "").strip()
        if not cleaned:
            continue
        by_key.setdefault(cleaned.lower(), []).append(cleaned)

    def _preferred(variants: list[str]) -> str:
        # not-lowercase first, then alphabetical — deterministic either way.
        return sorted(variants, key=lambda v: (v.islower(), v))[0]

    return sorted((_preferred(v) for v in by_key.values()), key=natural_sort.key)


# Sentinel for "this asset has no location recorded at all". Those rows match
# no location filter -- they are not at any location -- so before this existed
# they were visible only in the unfiltered register. That is what made a
# location count disagree with a spreadsheet with no way to see which rows were
# responsible: the total counted them, every filtered view silently did not.
# The value is not a legal location name, so it cannot collide with real data.
NO_LOCATION = "__none__"


def _collect_assets(db: Session, user: User, q: str, department: str,
                    location: str, compliant: str, online: str) -> dict:
    """Build the Asset Register rows for one set of filters.

    Shared by the HTML view and the CSV export so the file a reconciler
    downloads is by construction the same set of rows they were looking at.
    Exporting through a second, similar-but-separate query is what lets the two
    drift apart, which for a reconciliation tool is the one failure that
    matters.
    """
    from datetime import datetime, timedelta, timezone as _tz
    OFFLINE_THRESHOLD_HOURS = 24
    LAGGING_THRESHOLD_HOURS = 2
    now_utc = datetime.now(_tz.utc)
    offline_cutoff = now_utc - timedelta(hours=OFFLINE_THRESHOLD_HOURS)
    lagging_cutoff = now_utc - timedelta(hours=LAGGING_THRESHOLD_HOURS)

    def _online_state(last_seen):
        if not last_seen:
            return "offline"
        if last_seen < offline_cutoff:
            return "offline"
        if last_seen < lagging_cutoff:
            return "lagging"
        return "online"

    needle = (q or "").strip().lower()

    # Both sides of a dimension match are trimmed and lowercased. Untrimmed
    # comparison let "Achhad" and "Achhad " behave as two different places:
    # they appear as two entries in the dropdown, each holding part of the
    # count, and neither agrees with the site's own total.
    want_dept = (department or "").strip().lower()
    want_loc = (location or "").strip().lower()
    want_unlocated = want_loc == NO_LOCATION

    def _dim_excluded(value: str | None, wanted: str) -> bool:
        return bool(wanted) and (value or "").strip().lower() != wanted

    def _loc_excluded(value: str | None) -> bool:
        if want_unlocated:
            return bool((value or "").strip())
        return _dim_excluded(value, want_loc)

    agents = db.query(Agent).filter(Agent.tenant_id == user.tenant_id, Agent.uninstall_pending.is_(False)).all()

    # Matching key, not a rename — see services/hostnames. Case- and FQDN-
    # insensitive, so an agent reporting "LAP-01" and Entra reporting
    # "lap-01.tema.local" are recognised as one endpoint instead of listing the
    # machine twice. The empty key means "unknown" and must not match anything.
    managed_hostnames = {normalise_hostname(a.hostname) for a in agents if a.hostname}
    managed_hostnames.discard("")

    # Compliance comes from the endpoint's Entra record — including for
    # managed endpoints, whose Entra row is suppressed from the discovered
    # list below but still holds the verdict. Without this the Compliance
    # filter could only ever act on unmanaged devices, while the managed table
    # and its count sat unchanged whichever value was picked.
    entra_devices_q = (db.query(EntraDevice)
                         .filter(EntraDevice.tenant_id == user.tenant_id).all())
    compliance_by_host = compliance.by_hostname(entra_devices_q)

    def _compliance_excluded(value: bool | None) -> bool:
        return compliance.excluded(compliant, value)

    # Nothing to filter on until Entra has actually reported a verdict — with
    # no connector the column is all NULL, every pick returns an empty table,
    # and the reason is invisible. The template says so rather than leaving it
    # to be read as a bug.
    compliance_data_available = any(v is not None for v in compliance_by_host.values())
    managed_total = len(agents)
    discovered_total = sum(1 for d in entra_devices_q
                           if normalise_hostname(d.display_name) not in managed_hostnames)

    def _matches(*fields: str | None) -> bool:
        """Free-text needle test across the joined values of fields."""
        if not needle:
            return True
        hay = " | ".join((f or "").lower() for f in fields)
        return needle in hay

    # Online-state KPI counts — derived from ALL managed agents in the tenant,
    # not filtered, so the strip is a stable rollup of the fleet.
    online_count = lagging_count = offline_count = 0
    for a in agents:
        st = _online_state(a.last_seen_at)
        if st == "online": online_count += 1
        elif st == "lagging": lagging_count += 1
        else: offline_count += 1

    # Endpoints carrying no location at all, counted across both tables. The
    # register reports this so the gap between a site count and a spreadsheet
    # has a visible cause on screen rather than being left to be discovered by
    # subtraction.
    unlocated_total = 0

    rows = []
    for a in agents:
        snap = _latest_snapshot(db, a.id)
        payload = snap.payload if snap else {}
        pu = a.primary_user
        loc = a.location or (pu.location if pu else None)
        dept = pu.department if pu else None
        state = _online_state(a.last_seen_at)
        is_compliant = compliance_by_host.get(normalise_hostname(a.hostname))
        if not (loc or "").strip():
            unlocated_total += 1
        row = {
            "id": a.id,
            "hostname": a.hostname,
            "machine_id": a.machine_id,
            "os": payload.get("os", {}).get("caption", "—"),
            "cpu": payload.get("cpu", {}).get("name", "—"),
            "ram_gb": payload.get("memory", {}).get("total_gb"),
            "logged_in_user": payload.get("logged_in_user") or "—",
            "assigned_name":  (pu.full_name if pu else None) or (pu.email if pu else None),
            "assigned_id":    pu.id if pu else None,
            "department":     dept,
            "location":       loc,
            "last_seen_at":   a.last_seen_at,
            "online_state":   state,
            "software_count": len(payload.get("software", [])),
            "is_compliant":   is_compliant,
        }
        if _dim_excluded(dept, want_dept):                            continue
        if _loc_excluded(loc):                                        continue
        if online     and state != online:                            continue
        if _compliance_excluded(is_compliant):                        continue
        if not _matches(a.hostname, row["assigned_name"], dept, loc, row["os"]):
            continue
        rows.append(row)

    # Entra-discovered Windows endpoints that DON'T already have an OctoAssist
    # agent reporting (matched on the normalised hostname — case- and
    # FQDN-insensitive).
    discovered_rows = []
    for d in entra_devices_q:
        if normalise_hostname(d.display_name) in managed_hostnames:
            continue
        pu = d.primary_user
        # Device-level value wins, then the assigned user's — same precedence
        # as managed agents above (Agent.location or User.location), so a
        # manually-added asset shows the same detail as an imported one.
        dept = d.department or (pu.department if pu else None)
        loc  = d.location   or (pu.location   if pu else None)
        if not (loc or "").strip():
            unlocated_total += 1
        if _dim_excluded(dept, want_dept):                            continue
        if _loc_excluded(loc):                                        continue
        if _compliance_excluded(d.is_compliant):                      continue
        if not _matches(d.display_name, (pu.full_name if pu else None) or (pu.email if pu else None),
                        dept, loc, d.operating_system, d.manufacturer, d.model):
            continue
        discovered_rows.append({
            "id": d.id,
            "hostname": d.display_name,
            "machine_id": "",
            "assigned_name": (pu.full_name if pu else None) or (pu.email if pu else None),
            "department": dept,
            "location":   loc,
            "os":         d.operating_system or "—",
            "os_version": d.os_version or "—",
            "manufacturer": d.manufacturer or "—",
            "model":      d.model or "—",
            "is_compliant": d.is_compliant,
            "last_signin_at": d.approx_last_signin_at,
        })

    # Human order, not byte order: "TEMA-PC-2" before "TEMA-PC-10". Sorted here
    # rather than in SQL because the two tables are merged in Python and only
    # one of them is a database ordering to begin with.
    rows.sort(key=lambda r: natural_sort.key(r["hostname"]))
    discovered_rows.sort(key=lambda r: natural_sort.key(r["hostname"]))

    # Distinct values for filter dropdowns — from the full tenant set so admin
    # can always pivot. Union of agent + entra-device dimensions.
    from sqlalchemy import distinct
    dept_set = set()
    loc_set  = set()
    for (val,) in db.query(distinct(User.department)).filter(
            User.tenant_id == user.tenant_id, User.department.is_not(None)).all():
        if val: dept_set.add(val)
    for (val,) in db.query(distinct(Agent.location)).filter(
            Agent.tenant_id == user.tenant_id, Agent.location.is_not(None)).all():
        if val: loc_set.add(val)
    for (val,) in db.query(distinct(User.location)).filter(
            User.tenant_id == user.tenant_id, User.location.is_not(None)).all():
        if val: loc_set.add(val)
    # Device-level values too, else a manually-added asset's location or
    # department can be shown in the table but be missing from the filters.
    for (val,) in db.query(distinct(EntraDevice.location)).filter(
            EntraDevice.tenant_id == user.tenant_id, EntraDevice.location.is_not(None)).all():
        if val: loc_set.add(val)
    for (val,) in db.query(distinct(EntraDevice.department)).filter(
            EntraDevice.tenant_id == user.tenant_id, EntraDevice.department.is_not(None)).all():
        if val: dept_set.add(val)

    departments = _fold_variants(dept_set)
    locations   = _fold_variants(loc_set)

    return dict(
        rows=rows, discovered_rows=discovered_rows,
        departments=departments, locations=locations,
        q=needle,
        compliance_data_available=compliance_data_available,
        managed_total=managed_total, discovered_total=discovered_total,
        unlocated_total=unlocated_total,
        online_count=online_count, lagging_count=lagging_count,
        offline_count=offline_count,
        offline_threshold_hours=OFFLINE_THRESHOLD_HOURS,
        lagging_threshold_hours=LAGGING_THRESHOLD_HOURS,
    )


@router.get("/assets", response_class=HTMLResponse)
def assets_index(
    request: Request,
    flash: str | None = None,
    error: str | None = None,
    q: str = "",
    department: str = "",
    location: str = "",
    compliant: str = "",
    online: str = "",
    page: int = 1,
    dpage: int = 1,
    per_page: int = paging.DEFAULT_PER_PAGE,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    data = _collect_assets(db, user, q, department, location, compliant, online)

    # Two independent tables, two page params. The counts in the header keep
    # using the full sets; only what is rendered is sliced.
    pg_rows = paging.paginate(data["rows"], page, per_page)
    pg_disc = paging.paginate(data["discovered_rows"], dpage, per_page)
    data = {**data, "rows": pg_rows.items, "discovered_rows": pg_disc.items,
            "pg_rows": pg_rows, "pg_disc": pg_disc}

    entra_idp = (db.query(IdentityProvider)
                   .filter(IdentityProvider.tenant_id == user.tenant_id,
                           IdentityProvider.kind == IdentityProviderKind.entra,
                           IdentityProvider.is_enabled == True)  # noqa: E712
                   .first())

    return templates.TemplateResponse(
        request=request, name="assets_list.html",
        context=_ctx(user, db,
                     agent_count=pg_rows.total,
                     entra_idp=entra_idp,
                     no_location_value=NO_LOCATION,
                     filter_department=department, filter_location=location,
                     filter_compliant=compliant, filter_online=online,
                     export_query=urlencode({k: v for k, v in (
                         ("q", q), ("department", department), ("location", location),
                         ("compliant", compliant), ("online", online)) if v}),
                     flash=flash, error=error, **data),
    )


@router.get("/assets/export.csv")
def assets_export_csv(
    q: str = "",
    department: str = "",
    location: str = "",
    compliant: str = "",
    online: str = "",
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """The register as it is currently filtered, as CSV.

    Exists so a site owner can diff OctoAssist against their own spreadsheet
    instead of comparing two counts and being told only that they differ. The
    `source` column distinguishes an endpoint the agent reports from one only
    Entra has seen, since those two go missing for different reasons.
    """
    data = _collect_assets(db, user, q, department, location, compliant, online)

    def _fmt(dt):
        return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if dt else ""

    def _plain(value):
        """Empty cell, not the table's em-dash placeholder.

        A spreadsheet treats "—" as a value: it sorts, it fails an ISBLANK, and
        it survives a de-duplicate. The dash is a reading aid for the HTML
        table and has no business in an export meant for reconciliation.
        """
        v = (value or "").strip()
        return "" if v in {"—", "-"} else v

    def gen():
        for r in data["rows"]:
            yield ["agent", r["hostname"], _plain(r.get("machine_id")),
                   _plain(r["assigned_name"]), _plain(r["department"]), _plain(r["location"]),
                   _plain(r["os"]), r["online_state"], _fmt(r["last_seen_at"])]
        for d in data["discovered_rows"]:
            yield ["entra-discovered", d["hostname"], "",
                   _plain(d["assigned_name"]), _plain(d["department"]), _plain(d["location"]),
                   _plain(d["os"]), "", _fmt(d["last_signin_at"])]

    # Name the file after the filter, so a folder of exports for several sites
    # is still readable a week later.
    slug = re.sub(r"[^A-Za-z0-9]+", "-", (location or "all-locations")).strip("-").lower()
    return csv_export.stream(
        gen(),
        ["source", "hostname", "machine_id", "assigned_to", "department",
         "location", "operating_system", "online_state", "last_seen_at"],
        filename=f"octoassist-assets-{slug or 'all'}.csv",
    )

@router.post("/assets/bulk-delete-managed")
async def bulk_delete_managed(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Initiate self-uninstallation of selected OctoAssist agents and soft-delete them from UI.
    Dispatches a detached self-uninstall powershell command on check-in and emails Arun strictly.
    """
    form = await request.form()
    raw_ids = form.getlist("agent_ids")
    try:
        ids = [int(x) for x in raw_ids if str(x).strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad agent_ids")
    if not ids:
        return RedirectResponse(url="/assets?error=No+endpoints+selected", status_code=303)
        
    agents = db.query(Agent).filter(Agent.id.in_(ids), Agent.tenant_id == user.tenant_id).all()
    n = 0
    from ..models import RemoteAction, RemoteActionKind, RemoteActionStatus
    from ..services.notifications import agent_uninstallation_triggered
    from urllib.parse import quote
    
    # Detached self-uninstall PowerShell payload
    uninstall_script = (
        "$cmd = \"Start-Sleep -Seconds 5; Unregister-ScheduledTask -TaskName 'OctoAssistAgent' -Confirm:`$false; "
        "Remove-Item -Recurse -Force '$env:ProgramFiles\\OctoAssist Agent', '$env:ProgramData\\OctoAssist'\"\n"
        "Start-Process powershell.exe -ArgumentList \"-NoProfile -Command `\"$cmd`\"\" -WindowStyle Hidden"
    )
    
    for agent in agents:
        agent.uninstall_pending = True
        
        # Queue the uninstallation remote action
        action = RemoteAction(
            tenant_id=user.tenant_id,
            agent_id=agent.id,
            kind=RemoteActionKind.custom_powershell,
            params={
                "label": "Self-Uninstall OctoAssist Agent",
                "script": uninstall_script
            },
            status=RemoteActionStatus.pending,
            created_by_id=user.id
        )
        db.add(action)
        
        # Dispatch email alert to arun.d@temaindia.com
        try:
            agent_uninstallation_triggered(db, agent)
        except Exception:
            pass
            
        n += 1
        
    db.commit()
    return RedirectResponse(
        url=f"/assets?flash={quote(f'Triggered uninstallation on {n} managed endpoint(s). Notification sent.')}",
        status_code=303,
    )



@router.post("/assets/bulk-delete-discovered")
async def bulk_delete_discovered(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete selected EntraDevice rows (discovered-but-unmanaged endpoints).
    If the device still exists in Entra, it'll reappear on the next sync. Use
    this to hide retired or out-of-scope devices from the coverage list."""
    form = await request.form()
    raw_ids = form.getlist("device_ids")
    try:
        ids = [int(x) for x in raw_ids if str(x).strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad device_ids")
    if not ids:
        return RedirectResponse(url="/assets?error=No+devices+selected", status_code=303)
    n = (db.query(EntraDevice)
           .filter(EntraDevice.id.in_(ids), EntraDevice.tenant_id == user.tenant_id)
           .delete(synchronize_session=False))
    db.commit()
    return RedirectResponse(
        url=f"/assets?flash={quote(f'Deleted {n} discovered device(s). Will reappear on next Entra sync if still present.')}",
        status_code=303,
    )


@router.post("/assets/manual-add")
def manual_add_device(
    hostname: str = Form(...),
    primary_user_email: str = Form(""),
    operating_system: str = Form("Windows"),
    os_version: str = Form(""),
    location: str = Form(""),
    department: str = Form(""),
    manufacturer: str = Form(""),
    model: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Manually add a Windows endpoint to the discovered list. Useful for
    laptops that aren't in Entra yet (BYOD trial, just-bought, etc.) but you
    want to track. Creates an EntraDevice row with a manual_<uuid> id."""
    import uuid as _uuid
    from datetime import datetime, timezone
    hn = (hostname or "").strip()
    if not hn:
        return RedirectResponse(url="/assets?error=Hostname+is+required", status_code=303)
    # Dedupe case-insensitively — everywhere else hostnames are compared with
    # .strip().lower() (see managed_hostnames in the /assets view), so an
    # exact-match check here would let 'lap-01' and 'LAP-01' both exist and
    # then behave inconsistently across screens.
    existing = (db.query(EntraDevice)
                  .filter(EntraDevice.tenant_id == user.tenant_id,
                          func.lower(func.trim(EntraDevice.display_name)) == hn.lower())
                  .first())
    if existing:
        return RedirectResponse(
            url=f"/assets?error={quote(f'A device named {hn!r} already exists in the discovered list')}",
            status_code=303,
        )
    # Also refuse a hostname already reporting as a managed agent: the row
    # would be suppressed from the discovered list (agent data wins) and look
    # like the add silently failed.
    agent_clash = (db.query(Agent)
                     .filter(Agent.tenant_id == user.tenant_id,
                             func.lower(func.trim(Agent.hostname)) == hn.lower())
                     .first())
    if agent_clash:
        return RedirectResponse(
            url=f"/assets?error={quote(f'{hn!r} is already a managed OctoAssist endpoint — see the managed list above')}",
            status_code=303,
        )
    pu_id = None
    if primary_user_email.strip():
        pu = (db.query(User)
                .filter(User.tenant_id == user.tenant_id,
                        User.email == primary_user_email.strip().lower()).first())
        if pu:
            pu_id = pu.id
    d = EntraDevice(
        tenant_id=user.tenant_id,
        entra_device_id=f"manual-{_uuid.uuid4().hex}",
        display_name=hn[:255],
        operating_system=(operating_system or "Windows")[:60],
        os_version=(os_version or "").strip()[:60] or None,
        manufacturer=(manufacturer or None) and manufacturer.strip()[:120] or None,
        model=(model or None) and model.strip()[:120] or None,
        account_enabled=True,
        primary_user_id=pu_id,
        location=(location or "").strip()[:200] or None,
        department=(department or "").strip()[:200] or None,
        synced_at=datetime.now(timezone.utc),
    )
    db.add(d); db.commit()
    return RedirectResponse(
        url=f"/assets?flash={quote(f'Manually added {hn} to the discovered list.')}",
        status_code=303,
    )


@router.post("/assets/sync-entra")
async def sync_entra_devices_endpoint(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Pull every Windows device from the linked Entra tenant and upsert into
    the entra_devices table. Discovered devices show up in /assets under
    "Discovered (no agent yet)" so admins can see coverage gaps.
    """
    idp = (db.query(IdentityProvider)
             .filter(IdentityProvider.tenant_id == user.tenant_id,
                     IdentityProvider.kind == IdentityProviderKind.entra,
                     IdentityProvider.is_enabled == True)  # noqa: E712
             .first())
    if idp is None:
        return RedirectResponse(
            url="/assets?error=No+enabled+Entra+identity+provider.",
            status_code=303,
        )
    cfg = parse_entra_config(idp.config or {})
    report = await entra_devices.sync_devices(db, tenant_id=user.tenant_id, cfg=cfg)
    if report.errors:
        head = report.errors[0][:240]
        return RedirectResponse(
            url=f"/assets?error={quote(f'Device sync had errors. {report.summary()} — first: {head}')}",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/assets?flash={quote(f'Synced Windows devices from Entra: {report.summary()}')}",
        status_code=303,
    )


@router.get("/asset/{agent_id}", response_class=HTMLResponse)
def asset_detail(
    agent_id: int,
    request: Request,
    flash: str | None = None,
    error: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    agent = db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Agent not found")
    snap = _latest_snapshot(db, agent.id)
    
    from ..models import SoftwarePackage
    catalog = (db.query(SoftwarePackage)
                 .filter(SoftwarePackage.tenant_id == user.tenant_id,
                         SoftwarePackage.is_active.is_(True))
                 .order_by(SoftwarePackage.sort_order, SoftwarePackage.name).all())

    return templates.TemplateResponse(
        request=request, name="asset_detail.html",
        context=_ctx(user, db,
                     agent=agent,
                     snapshot=snap.payload if snap else None,
                     snapshot_at=snap.snapshot_at if snap else None,
                     catalog=catalog,
                     flash=flash,
                     error=error),
    )


@router.get("/asset/{agent_id}/software/export.csv")
def asset_software_export(
    agent_id: int,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    from fastapi.responses import StreamingResponse
    import csv
    from io import StringIO

    agent = db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Agent not found")

    snap = _latest_snapshot(db, agent.id)
    software_list = []
    if snap and snap.payload and isinstance(snap.payload, dict):
        software_list = snap.payload.get("software", [])
        if not isinstance(software_list, list):
            software_list = []

    output = StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow(["Name", "Version", "Publisher", "Install Date"])

    # Data rows
    for s in software_list:
        writer.writerow([
            s.get("name", ""),
            s.get("version", "—"),
            s.get("publisher", "—"),
            s.get("install_date", "—")
        ])

    body = output.getvalue()
    safe_filename = f"{agent.hostname}_installed_software.csv".replace(" ", "_").replace("/", "_")

    return StreamingResponse(
        iter([body]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )


@router.get("/enrolment", response_class=HTMLResponse)
def enrolment(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    tenant = db.query(Tenant).first()
    return templates.TemplateResponse(
        request=request, name="enrolment.html",
        context={"current_user": user, "tenant": tenant},
    )
