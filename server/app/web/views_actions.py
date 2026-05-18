"""Remote-action dashboard routes.

  GET  /actions                         — tenant-wide history with kind/status filters
  GET  /asset/{id}/actions              — per-endpoint history
  GET  /asset/{id}/processes            — latest list_processes result + kill buttons
  POST /asset/{id}/action               — queue a generic action (kind + params)
  POST /asset/{id}/processes/refresh    — queue a list_processes refresh
  POST /asset/{id}/processes/kill       — queue a kill_process action
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import require_admin, require_staff
from ..database import get_db
from ..jinja_filters import install_on
from ..models import (
    Agent, RemoteAction, RemoteActionKind, RemoteActionStatus, Tenant, User,
)
from ..services import remote_actions as ra_svc

from sqlalchemy.orm import Session

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(templates)

router = APIRouter(tags=["actions"])


def _ctx(user: User, db: Session, **extra) -> dict:
    return {"current_user": user, "tenant": db.query(Tenant).first(), **extra}


# ---------- Tenant-wide history ----------

@router.get("/actions", response_class=HTMLResponse)
def actions_list(
    request: Request,
    kind: str | None = None,
    status: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    q = (db.query(RemoteAction)
           .filter(RemoteAction.tenant_id == user.tenant_id))
    if kind:
        try:
            q = q.filter(RemoteAction.kind == RemoteActionKind(kind))
        except ValueError:
            pass
    if status:
        try:
            q = q.filter(RemoteAction.status == RemoteActionStatus(status))
        except ValueError:
            pass
    actions = (q.order_by(RemoteAction.created_at.desc()).limit(200).all())
    return templates.TemplateResponse(
        request=request, name="actions_list.html",
        context=_ctx(user, db,
                     actions=actions,
                     kinds=[k.value for k in RemoteActionKind],
                     statuses=[s.value for s in RemoteActionStatus],
                     filter_kind=kind or "", filter_status=status or "",
                     ra_svc=ra_svc),
    )


# ---------- Per-asset action queue ----------

@router.post("/asset/{agent_id}/action")
async def asset_action_queue(
    agent_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Single generic POST endpoint for every action kind. The form
    submits 'kind' + any kind-specific fields; we assemble the params
    dict here based on the kind."""
    form = await request.form()
    kind_str = (form.get("kind") or "").strip()
    try:
        kind = RemoteActionKind(kind_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unknown action kind: {kind_str}")

    # Build params per-kind. Anything not in the allow-list is dropped.
    params: dict = {}
    if kind == RemoteActionKind.run_executable:
        params = {"label": form.get("label", "")[:120],
                  "url": (form.get("url") or "").strip(),
                  "args": (form.get("args") or "").strip()}
    elif kind == RemoteActionKind.reboot:
        try:    params["delay_seconds"] = max(0, int(form.get("delay_seconds", 60)))
        except (TypeError, ValueError): params["delay_seconds"] = 60
        params["reason"] = (form.get("reason") or "OctoAssist-initiated reboot")[:120]
    elif kind == RemoteActionKind.lock_workstation:
        params = {}
    elif kind == RemoteActionKind.send_toast:
        params = {"title": (form.get("title") or "OctoAssist")[:80],
                  "body":  (form.get("body")  or "")[:500]}
    elif kind == RemoteActionKind.list_processes:
        params = {}
    elif kind == RemoteActionKind.kill_process:
        n = (form.get("name") or "").strip()
        p = (form.get("pid")  or "").strip()
        if not n and not p:
            raise HTTPException(status_code=400, detail="name or pid required")
        if n: params["name"] = n
        if p:
            try: params["pid"] = int(p)
            except ValueError: raise HTTPException(status_code=400, detail="pid must be int")
    elif kind == RemoteActionKind.set_wallpaper:
        # Accept either an uploaded image OR a pasted URL
        url = (form.get("url") or "").strip()
        wallpaper_file = form.get("wallpaper_file")
        file_supplied = (hasattr(wallpaper_file, "filename") and wallpaper_file.filename
                         and wallpaper_file.size and wallpaper_file.size > 0)
        if file_supplied:
            from ..api.uploads import UPLOAD_DIR, _safe_ext, MAX_BYTES
            from ..models import UploadedFile as _UF
            import hashlib as _hl, uuid as _uuid
            file_id = _uuid.uuid4().hex
            ext = _safe_ext(wallpaper_file.filename)
            target_path = UPLOAD_DIR / f"{file_id}{ext}"
            h = _hl.sha256(); written = 0
            try:
                with open(target_path, "wb") as out:
                    while True:
                        chunk = await wallpaper_file.read(1024 * 256)
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
            db.add(_UF(
                id=file_id, tenant_id=user.tenant_id,
                original_filename=wallpaper_file.filename[:255],
                content_type=(wallpaper_file.content_type or "image/jpeg")[:120],
                size_bytes=written, sha256=h.hexdigest(),
                purpose="wallpaper", created_by_id=user.id,
            ))
            db.commit()
            base = str(request.base_url).rstrip("/")
            url = f"{base}/files/{file_id}{ext}"
        if not url:
            raise HTTPException(status_code=400, detail="Upload an image or paste a URL")
        params = {"url": url}
    elif kind == RemoteActionKind.reset_password:
        params = {"username":     (form.get("username") or "").strip(),
                  "new_password":  form.get("new_password") or ""}
    elif kind == RemoteActionKind.custom_powershell:
        params = {"script": form.get("script") or ""}
    elif kind == RemoteActionKind.force_refresh:
        params = {}   # no params; agent does the work

    try:
        action = ra_svc.queue(db,
                              tenant_id=user.tenant_id, creator=user,
                              agent_id=agent_id, kind=kind, params=params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return RedirectResponse(
        url=f"/asset/{agent_id}/actions?flash=Queued+{kind.value}+(#{action.id})",
        status_code=303,
    )


# ---------- Per-asset history ----------

@router.get("/asset/{agent_id}/actions", response_class=HTMLResponse)
def asset_actions(
    agent_id: int,
    request: Request,
    flash: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    agent = db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    actions = (db.query(RemoteAction)
                 .filter(RemoteAction.agent_id == agent_id,
                         RemoteAction.tenant_id == user.tenant_id)
                 .order_by(RemoteAction.created_at.desc())
                 .limit(100).all())
    return templates.TemplateResponse(
        request=request, name="asset_actions.html",
        context=_ctx(user, db, agent=agent, actions=actions, flash=flash,
                     ra_svc=ra_svc),
    )


# ---------- Per-asset processes ----------

@router.get("/asset/{agent_id}/processes", response_class=HTMLResponse)
def asset_processes(
    agent_id: int,
    request: Request,
    flash: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    agent = db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)

    # Latest completed list_processes action for this agent
    latest = (db.query(RemoteAction)
                .filter(RemoteAction.agent_id == agent_id,
                        RemoteAction.tenant_id == user.tenant_id,
                        RemoteAction.kind == RemoteActionKind.list_processes,
                        RemoteAction.status == RemoteActionStatus.succeeded)
                .order_by(RemoteAction.created_at.desc()).first())
    pending = (db.query(RemoteAction)
                 .filter(RemoteAction.agent_id == agent_id,
                         RemoteAction.tenant_id == user.tenant_id,
                         RemoteAction.kind == RemoteActionKind.list_processes,
                         RemoteAction.status.in_(
                             [RemoteActionStatus.pending,
                              RemoteActionStatus.in_progress]))
                 .order_by(RemoteAction.created_at.desc()).first())

    processes = []
    if latest and latest.result:
        processes = (latest.result.get("processes") or [])
    return templates.TemplateResponse(
        request=request, name="asset_processes.html",
        context=_ctx(user, db, agent=agent,
                     processes=processes,
                     scan_time=(latest.finished_at if latest else None),
                     pending_refresh=pending,
                     flash=flash),
    )


@router.post("/asset/{agent_id}/processes/refresh")
def asset_processes_refresh(
    agent_id: int,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    try:
        ra_svc.queue(db, tenant_id=user.tenant_id, creator=user,
                     agent_id=agent_id, kind=RemoteActionKind.list_processes,
                     params={})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(
        url=f"/asset/{agent_id}/processes?flash=Refresh+queued+(arrives+in+~30+sec)",
        status_code=303,
    )


@router.post("/asset/{agent_id}/processes/kill")
def asset_processes_kill(
    agent_id: int,
    name: str = Form(""),
    pid:  str = Form(""),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    params: dict = {}
    name = name.strip(); pid = pid.strip()
    if name: params["name"] = name
    if pid:
        try: params["pid"] = int(pid)
        except ValueError: raise HTTPException(status_code=400, detail="pid must be int")
    if not params:
        raise HTTPException(status_code=400, detail="name or pid required")
    try:
        ra_svc.queue(db, tenant_id=user.tenant_id, creator=user,
                     agent_id=agent_id, kind=RemoteActionKind.kill_process,
                     params=params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(
        url=f"/asset/{agent_id}/processes?flash=Kill+queued+for+{name or pid}",
        status_code=303,
    )
