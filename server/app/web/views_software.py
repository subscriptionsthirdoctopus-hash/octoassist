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

from ..auth import require_staff
from ..database import get_db
from ..models import Agent, RemoteAction, RemoteActionKind, Tenant, User
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
                .filter(Agent.tenant_id == user.tenant_id)
                .order_by(Agent.hostname).all())
    actions = ra_svc.recent(db, tenant_id=user.tenant_id,
                            kind=RemoteActionKind.run_executable, limit=50)
    return templates.TemplateResponse(
        request=request, name="software_deploy.html",
        context=_ctx(user, db, agents=agents, actions=actions,
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
    from ..api.uploads import UPLOAD_DIR
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
        target_path = UPLOAD_DIR / f"{file_id}{ext}"
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
        base = str(request.base_url).rstrip("/")
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
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    publisher = unquote(publisher)
    product   = unquote(product)
    detail = sam.product_detail(db, user.tenant_id, publisher, product)
    if detail is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request, name="software_detail.html",
        context=_ctx(user, db,
                     detail=detail,
                     license_labels=LICENSE_LABEL),
    )
