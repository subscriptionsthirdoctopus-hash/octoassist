"""Software Asset Management (SAM) views.

Routes:
    GET  /software                              — fleet rollup with category + license filters
    GET  /software/product/{publisher}/{product} — per-product detail (endpoint list)
    GET  /software/export.csv                    — long-form CSV for SAM audits
"""
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from ..jinja_filters import install_on
from sqlalchemy.orm import Session

from ..auth import require_staff, require_admin
from ..database import get_db
from ..models import Agent, RemoteAction, RemoteActionKind, SoftwarePackage, Tenant, User
from ..services import charts, remote_actions as ra_svc, sam

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(templates)

router = APIRouter(tags=["software"])


def _ctx(user: User, db: Session, **extra) -> dict:
    return {"current_user": user, "tenant": db.query(Tenant).first(), **extra}


# Pretty labels for the license_posture enum-ish strings.
LICENSE_LABEL = {
    "licensed_paid":  "Licensed (paid)",
    "licensed_oem":   "Licensed (OEM)",
    "free_personal":  "Free personal / paid business",
    "freeware_oss":   "Freeware / OSS",
    "unknown":        "Unknown — review",
}


@router.get("/software", response_class=HTMLResponse)
def software_home(
    request: Request,
    category: str = Query(""),
    license_filter: str = Query("", alias="license"),
    publisher: str = Query(""),
    q: str = Query(""),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    tid = user.tenant_id
    all_rows = sam.fleet_software(db, tid)
    kpi = sam.fleet_kpis(db, tid)

    # Apply filters
    rows = all_rows
    if category:
        rows = [r for r in rows if r["category"] == category]
    if license_filter:
        rows = [r for r in rows if r["license_posture"] == license_filter]
    if publisher:
        rows = [r for r in rows if r["publisher"].lower() == publisher.lower()]
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r["product"].lower() or ql in r["publisher"].lower()]

    # Chart data — derived from FILTERED rows so the charts respond to filters
    cat_data = sam.category_breakdown(rows)
    pub_data = sam.publisher_breakdown(rows, top=15)

    # Distinct filter lists drawn from the FULL, unfiltered roll-up so the
    # user can always pivot back out.
    categories = sorted({r["category"] for r in all_rows})
    publishers = sorted({r["publisher"] for r in all_rows}, key=str.lower)

    return templates.TemplateResponse(
        request=request, name="software_list.html",
        context=_ctx(user, db,
                     rows=rows,
                     all_count=len(all_rows),
                     kpi=kpi,
                     categories=categories,
                     publishers=publishers,
                     license_labels=LICENSE_LABEL,
                     active_filters={
                         "category": category, "license": license_filter,
                         "publisher": publisher, "q": q,
                     },
                     chart_categories=charts.bars_h(cat_data,  width=540, label_w=220),
                     chart_publishers=charts.bars_h(pub_data, width=540, label_w=220)),
    )


@router.get("/software/export.csv")
def software_export(
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    body = sam.export_csv(db, user.tenant_id)
    return StreamingResponse(
        iter([body]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="octoassist-software-sam.csv"'},
    )


# ---------- Software → Deploy tab (run .exe / .msi on endpoints) ----------

@router.get("/software/deploy", response_class=HTMLResponse)
def software_deploy_form(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
    flash: str | None = None,
    error: str | None = None,
):
    agents = (db.query(Agent)
                .filter(Agent.tenant_id == user.tenant_id, Agent.uninstall_pending.is_(False))
                .order_by(Agent.hostname).all())

    actions = ra_svc.recent(db, tenant_id=user.tenant_id,
                            kind=RemoteActionKind.run_executable, limit=50)
    catalog = (db.query(SoftwarePackage)
                 .filter(SoftwarePackage.tenant_id == user.tenant_id,
                         SoftwarePackage.is_active.is_(True))
                 .order_by(SoftwarePackage.sort_order, SoftwarePackage.name).all())
    return templates.TemplateResponse(
        request=request, name="software_deploy.html",
        context=_ctx(user, db, agents=agents, actions=actions, catalog=catalog,
                     flash=flash, error=error),
    )


@router.post("/software/deploy")
async def software_deploy_submit(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Accept either an uploaded installer file OR a pasted URL. Multipart form."""
    from fastapi import UploadFile
    from ..api.uploads import ensure_upload_dir
    import hashlib, os, uuid as _uuid
    from ..models import UploadedFile

    form = await request.form()
    label = (form.get("label") or "").strip()[:120]
    url   = (form.get("url") or "").strip()
    args  = (form.get("args") or "").strip()
    target_mode      = form.get("target_mode", "agent")
    agent_id_raw     = form.get("agent_id")
    hostname_pattern = form.get("hostname_pattern", "%")

    installer_file = form.get("installer_file")
    # An empty UploadFile means no file selected — its filename will be empty.
    file_supplied = (hasattr(installer_file, "filename") and installer_file.filename
                     and installer_file.size and installer_file.size > 0)

    if not file_supplied and not url:
        raise HTTPException(status_code=400, detail="Either upload a file OR paste a URL")
    if not label:
        raise HTTPException(status_code=400, detail="Label is required")

    # If an upload came in, save it and replace the URL with the public file URL
    if file_supplied:
        from ..api.uploads import _safe_ext, MAX_BYTES
        file_id = _uuid.uuid4().hex
        ext = _safe_ext(installer_file.filename)
        target_path = ensure_upload_dir() / f"{file_id}{ext}"
        h = hashlib.sha256()
        written = 0
        try:
            with open(target_path, "wb") as out:
                while True:
                    chunk = await installer_file.read(1024 * 256)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_BYTES:
                        out.close(); target_path.unlink(missing_ok=True)
                        raise HTTPException(status_code=413, detail="File too large (>1 GB)")
                    h.update(chunk); out.write(chunk)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            target_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"upload failed: {e}")

        db.add(UploadedFile(
            id=file_id, tenant_id=user.tenant_id,
            original_filename=installer_file.filename[:255],
            content_type=(installer_file.content_type or "application/octet-stream")[:120],
            size_bytes=written, sha256=h.hexdigest(),
            purpose="installer", created_by_id=user.id,
        ))
        db.commit()

        # Build absolute URL the agent will fetch
        from ..config import settings
        base = settings.base_url.rstrip("/")
        url = f"{base}/files/{file_id}{ext}"

    params = {
        "label": label,
        "url":   url,
        "args":  args,
    }
    queued = 0
    if target_mode == "agent":
        try:
            agent_id = int(agent_id_raw) if agent_id_raw else None
        except (TypeError, ValueError):
            agent_id = None
        if not agent_id:
            raise HTTPException(status_code=400, detail="Pick an endpoint")
        try:
            ra_svc.queue(db, tenant_id=user.tenant_id, creator=user,
                         agent_id=agent_id, kind=RemoteActionKind.run_executable,
                         params=params)
            queued = 1
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    elif target_mode == "select":
        # Multi-select: form sends multiple `agent_ids` checkbox values
        raw_ids = form.getlist("agent_ids") if hasattr(form, "getlist") else []
        ids: list[int] = []
        for s in raw_ids:
            try:
                ids.append(int(s))
            except (TypeError, ValueError):
                pass
        if not ids:
            raise HTTPException(status_code=400, detail="Pick at least one endpoint")
        # Verify tenant ownership in one go, then queue per agent
        owned = {a.id for a in db.query(Agent.id)
                                  .filter(Agent.tenant_id == user.tenant_id,
                                          Agent.id.in_(ids)).all()}
        for aid in ids:
            if aid in owned:
                try:
                    ra_svc.queue(db, tenant_id=user.tenant_id, creator=user,
                                 agent_id=aid, kind=RemoteActionKind.run_executable,
                                 params=params)
                    queued += 1
                except ValueError:
                    continue
    else:
        # 'pattern' or 'all' — both go through queue_for_fleet
        pattern = "%" if target_mode == "all" else (hostname_pattern or "%")
        actions = ra_svc.queue_for_fleet(
            db, tenant_id=user.tenant_id, creator=user,
            kind=RemoteActionKind.run_executable, params=params,
            hostname_pattern=pattern,
        )
        queued = len(actions)
    if queued == 0:
        return RedirectResponse(
            url="/software/deploy?error=No+endpoints+matched+that+pattern",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/software/deploy?flash=Queued+on+{queued}+endpoint(s).+Agents+pick+up+within+30+seconds.",
        status_code=303,
    )


@router.get("/software/deploy/{action_id}", response_class=HTMLResponse)
def software_deploy_detail(
    action_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    a = db.get(RemoteAction, action_id)
    if a is None or a.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request, name="software_deploy_detail.html",
        context=_ctx(user, db, action=a),
    )


@router.get("/software/product/{publisher}/{product}", response_class=HTMLResponse)
def software_product_detail(
    publisher: str,
    product: str,
    request: Request,
    flash: str | None = None,
    error: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    publisher = unquote(publisher)
    product   = unquote(product)
    detail = sam.product_detail(db, user.tenant_id, publisher, product)
    if detail is None:
        raise HTTPException(status_code=404)
        
    catalog = (db.query(SoftwarePackage)
                 .filter(SoftwarePackage.tenant_id == user.tenant_id,
                         SoftwarePackage.is_active.is_(True))
                 .order_by(SoftwarePackage.sort_order, SoftwarePackage.name).all())

    from ..models import AssetGroup
    groups = (db.query(AssetGroup)
                .filter(AssetGroup.tenant_id == user.tenant_id)
                .order_by(AssetGroup.name).all())

    return templates.TemplateResponse(
        request=request, name="software_detail.html",
        context=_ctx(user, db,
                     detail=detail,
                     catalog=catalog,
                     groups=groups,
                     license_labels=LICENSE_LABEL,
                     flash=flash,
                     error=error),
    )


@router.get("/software/product/{publisher}/{product}/export.csv")
def software_product_export(
    publisher: str,
    product: str,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    publisher = unquote(publisher)
    product   = unquote(product)
    detail = sam.product_detail(db, user.tenant_id, publisher, product)
    if detail is None:
        raise HTTPException(status_code=404)

    import csv
    from io import StringIO

    output = StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow(["Hostname", "Version", "Installed Date", "Last Seen", "As Reported Name"])

    # Data rows
    for e in detail.get("endpoints", []):
        last_seen = e.get("last_seen_at")
        last_seen_str = last_seen.strftime("%Y-%m-%d %H:%M:%S") if last_seen else "never"
        writer.writerow([
            e.get("hostname", ""),
            e.get("version", ""),
            e.get("install_date", ""),
            last_seen_str,
            e.get("raw_name", "")
        ])

    body = output.getvalue()
    safe_filename = f"{publisher}_{product}_installs.csv".replace(" ", "_").replace("/", "_")

    return StreamingResponse(
        iter([body]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )


    return StreamingResponse(
        iter([body]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )


# ---------- Admin-only Remote Action Triggers ----------

def build_dynamic_uninstall_script(software_name: str) -> str:
    """Generates a dynamic Windows registry-scanning PowerShell script that uninstalls
    the target application by name silently in the SYSTEM context with no user interaction.
    """
    return rf"""
$name = "{software_name}"
Write-Host "Searching for uninstall strings matching: $name"

$paths = @(
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKLM:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*"
)

$apps = Get-ItemProperty $paths -ErrorAction SilentlyContinue | Where-Object {{ 
    $_.DisplayName -like "*$name*" -or $_.PSChildName -like "*$name*"
}}

if (-not $apps) {{
    Write-Host "No installed applications found matching: $name"
    exit 0
}}

foreach ($app in $apps) {{
    $displayName = $app.DisplayName
    $uninstallString = $app.UninstallString
    Write-Host "Found app: $displayName"
    Write-Host "Uninstall string: $uninstallString"

    if (-not $uninstallString) {{
        Write-Host "No uninstall string found for $displayName"
        continue
    }}

    if ($uninstallString -match "msiexec") {{
        if ($uninstallString -match "\{{[A-F0-9]{{8}}-[A-F0-9]{{4}}-[A-F0-9]{{4}}-[A-F0-9]{{4}}-[A-F0-9]{{12}}\}}") {{
            $guid = $Matches[0]
            $cmd = "msiexec.exe /x $guid /quiet /norestart"
            Write-Host "Executing MSI silent uninstall: $cmd"
            Start-Process cmd.exe -ArgumentList "/c $cmd" -Wait -NoNewWindow
        }} else {{
            $cmd = $uninstallString -replace "/I", "/X" -replace "/i", "/x"
            if ($cmd -notlike "*/quiet*") {{ $cmd += " /quiet /norestart" }}
            Write-Host "Executing modified MSI uninstall: $cmd"
            Start-Process cmd.exe -ArgumentList "/c $cmd" -Wait -NoNewWindow
        }}
    }} else {{
        # Handle unquoted paths with spaces in the registry UninstallString
        $cmd = $uninstallString.Trim()
        $exe = ""
        $args = ""
        if ($cmd -match '^"([^"]+)"\s*(.*)$') {{
            $exe = $Matches[1]
            $args = $Matches[2]
        }} else {{
            # Split by space and find the longest prefix that represents an existing file
            $parts = $cmd -split ' '
            $found = $false
            for ($i = $parts.Count; $i -gt 0; $i--) {{
                $testExe = ($parts[0..($i-1)] -join ' ').Trim()
                $testExe = $testExe -replace '^"', '' -replace '"$', ''
                if (Test-Path $testExe -PathType Leaf) {{
                    $exe = $testExe
                    $args = ($parts[$i..$parts.Count] -join ' ').Trim()
                    $found = $true
                    break
                }}
            }}
            if (-not $found) {{
                # Fallback to splitting on the first space
                if ($cmd -match '^([^\s]+)\s*(.*)$') {{
                    $exe = $Matches[1]
                    $args = $Matches[2]
                }} else {{
                    $exe = $cmd
                    $args = ""
                }}
            }}
        }}

        $lowerExe = $exe.ToLower()
        $silentArgs = ""
        
        # Smart silent argument detection based on installer type
        if ($lowerExe -match "unins\d{{3}}\.exe" -or $lowerExe -contains "inno") {{
            $silentArgs = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
        }} elseif ($lowerExe -match "uninstall\.exe" -or $lowerExe -match "uninst\.exe" -or $lowerExe -contains "nsis") {{
            $silentArgs = "/S"
        }} elseif ($lowerExe -match "setup\.exe" -or $lowerExe -match "install\.exe") {{
            $silentArgs = "/s /S /quiet /qn /norestart /VERYSILENT /SUPPRESSMSGBOXES"
        }} else {{
            $silentArgs = "/S /s /quiet /qn /norestart /VERYSILENT"
        }}
        
        $finalArgs = ""
        if ($args) {{
            $finalArgs = "$args $silentArgs"
        }} else {{
            $finalArgs = $silentArgs
        }}

        Write-Host "Executing EXE silent uninstall: $exe $finalArgs"
        Start-Process $exe -ArgumentList $finalArgs -Wait -NoNewWindow -ErrorAction SilentlyContinue
    }}
}}
Write-Host "Uninstall process completed."
""".strip()


@router.post("/software/product/action")
async def software_product_action(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    publisher = (form.get("publisher") or "").strip()
    product = (form.get("product") or "").strip()
    action_type = (form.get("action_type") or "install").strip()
    target_mode = (form.get("target_mode") or "all").strip()
    hostname_pattern = (form.get("hostname_pattern") or "%").strip()

    if not publisher or not product:
        raise HTTPException(status_code=400, detail="Publisher and Product are required")

    pub_quoted = quote(publisher)
    prod_quoted = quote(product)

    # Automatically locate a matching catalog package by name
    package = (db.query(SoftwarePackage)
                 .filter(SoftwarePackage.tenant_id == user.tenant_id,
                         SoftwarePackage.is_active.is_(True),
                         SoftwarePackage.name.ilike(product))
                 .first())

    # Build params based on action_type
    if action_type in ("install", "update"):
        if not package:
            return RedirectResponse(
                url=f"/software/product/{pub_quoted}/{prod_quoted}?error=No+active+package+found+for+'{quote(product)}'+in+the+Software+Catalog.+Please+create+one+first+to+enable+remote+fleet+deployment.",
                status_code=303
            )
        kind = RemoteActionKind.run_executable
        params = {
            "label": f"{action_type.capitalize()} {package.name} {package.version}",
            "url": package.installer_url,
            "args": package.install_args
        }
        label_action = f"{action_type} of {package.name}"
    elif action_type == "uninstall":
        if package and package.uninstall_command:
            kind = RemoteActionKind.custom_powershell
            params = {
                "script": package.uninstall_command,
                "label": f"Uninstall {package.name}"
            }
            label_action = f"uninstall of {package.name}"
        else:
            # Dynamic registry uninstaller fallback
            kind = RemoteActionKind.custom_powershell
            params = {
                "script": build_dynamic_uninstall_script(product),
                "label": f"Dynamic Uninstall: {product}"
            }
            label_action = f"dynamic uninstallation of {product}"
    else:
        raise HTTPException(status_code=400, detail="Invalid action type")

    queued = 0
    if target_mode == "all":
        detail = sam.product_detail(db, user.tenant_id, publisher, product)
        if not detail or not detail.get("endpoints"):
            return RedirectResponse(
                url=f"/software/product/{pub_quoted}/{prod_quoted}?error=No+endpoints+found+with+this+software+installed",
                status_code=303
            )
        for e in detail["endpoints"]:
            try:
                ra_svc.queue(db, tenant_id=user.tenant_id, creator=user,
                             agent_id=e["agent_id"], kind=kind, params=params)
                queued += 1
            except Exception:
                continue
    elif target_mode == "select":
        raw_ids = form.getlist("agent_ids") if hasattr(form, "getlist") else []
        ids: list[int] = []
        for s in raw_ids:
            try:
                ids.append(int(s))
            except (TypeError, ValueError):
                pass
        if not ids:
            return RedirectResponse(
                url=f"/software/product/{pub_quoted}/{prod_quoted}?error=Pick+at+least+one+endpoint+via+checkboxes",
                status_code=303
            )
        owned = {a.id for a in db.query(Agent.id)
                                  .filter(Agent.tenant_id == user.tenant_id,
                                          Agent.id.in_(ids)).all()}
        for aid in ids:
            if aid in owned:
                try:
                    ra_svc.queue(db, tenant_id=user.tenant_id, creator=user,
                                 agent_id=aid, kind=kind, params=params)
                    queued += 1
                except Exception:
                    continue
    elif target_mode == "group":
        from ..models import AssetGroupMember
        group_id_str = form.get("group_id") or ""
        try:
            group_id = int(group_id_str)
        except ValueError:
            return RedirectResponse(
                url=f"/software/product/{pub_quoted}/{prod_quoted}?error=Invalid+asset+group+selected",
                status_code=303
            )
        # Query group members
        group_agents = (
            db.query(AssetGroupMember.agent_id)
            .filter(AssetGroupMember.group_id == group_id)
            .all()
        )
        ids = [a.agent_id for a in group_agents]
        if not ids:
            return RedirectResponse(
                url=f"/software/product/{pub_quoted}/{prod_quoted}?error=Selected+asset+group+has+no+member+machines",
                status_code=303
            )
        owned = {a.id for a in db.query(Agent.id)
                                  .filter(Agent.tenant_id == user.tenant_id,
                                          Agent.id.in_(ids)).all()}
        for aid in ids:
            if aid in owned:
                try:
                    ra_svc.queue(db, tenant_id=user.tenant_id, creator=user,
                                 agent_id=aid, kind=kind, params=params)
                    queued += 1
                except Exception:
                    continue
    elif target_mode == "pattern":
        pattern = hostname_pattern or "%"
        actions = ra_svc.queue_for_fleet(
            db, tenant_id=user.tenant_id, creator=user,
            kind=kind, params=params,
            hostname_pattern=pattern,
        )
        queued = len(actions)
    else:
        raise HTTPException(status_code=400, detail="Invalid target mode")

    return RedirectResponse(
        url=f"/software/product/{pub_quoted}/{prod_quoted}?flash=Successfully+queued+{label_action}+on+{queued}+endpoint(s)",
        status_code=303
    )


@router.post("/asset/{agent_id}/software/action")
async def asset_software_action(
    agent_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    software_name = (form.get("software_name") or "").strip()
    action_type = (form.get("action_type") or "install").strip()

    if not software_name:
        return RedirectResponse(
            url=f"/asset/{agent_id}?error=Software+name+is+required",
            status_code=303
        )

    # Automatically search for a matching software package in catalog
    package = (db.query(SoftwarePackage)
                 .filter(SoftwarePackage.tenant_id == user.tenant_id,
                         SoftwarePackage.is_active.is_(True),
                         SoftwarePackage.name.ilike(software_name))
                 .first())

    # Fallback to substring matching if needed
    if not package:
        package = (db.query(SoftwarePackage)
                     .filter(SoftwarePackage.tenant_id == user.tenant_id,
                             SoftwarePackage.is_active.is_(True),
                             (SoftwarePackage.name.ilike(f"%{software_name}%") | 
                              SoftwarePackage.name.ilike(software_name.split()[0] + "%")))
                     .first())

    # Build params based on action_type
    if action_type in ("install", "update"):
        if not package:
            return RedirectResponse(
                url=f"/asset/{agent_id}?error=No+active+catalog+package+found+matching+'{quote(software_name)}'.+Please+add+it+to+the+Software+Catalog+to+enable+remote+updates.",
                status_code=303
            )
        kind = RemoteActionKind.run_executable
        params = {
            "label": f"{action_type.capitalize()} {package.name} {package.version}",
            "url": package.installer_url,
            "args": package.install_args
        }
        label_action = f"{action_type} for {package.name}"
    elif action_type == "uninstall":
        if package and package.uninstall_command:
            kind = RemoteActionKind.custom_powershell
            params = {
                "script": package.uninstall_command,
                "label": f"Uninstall {package.name}"
            }
            label_action = f"uninstall of {package.name}"
        else:
            # Gracefully trigger dynamic registry-scanning uninstaller!
            kind = RemoteActionKind.custom_powershell
            params = {
                "script": build_dynamic_uninstall_script(software_name),
                "label": f"Dynamic Uninstall: {software_name}"
            }
            label_action = f"dynamic uninstallation of {software_name}"
    else:
        raise HTTPException(status_code=400, detail="Invalid action type")

    try:
        action = ra_svc.queue(db, tenant_id=user.tenant_id, creator=user,
                              agent_id=agent_id, kind=kind, params=params)
    except ValueError as e:
        return RedirectResponse(
            url=f"/asset/{agent_id}?error={quote(str(e))}",
            status_code=303
        )

    return RedirectResponse(
        url=f"/asset/{agent_id}?flash=Successfully+queued+{label_action}+%28action+%23{action.id}%29",
        status_code=303
    )
