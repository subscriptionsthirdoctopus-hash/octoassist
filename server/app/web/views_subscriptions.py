"""Software Subscriptions — owned licences, seats, POs, expiry and documents.

TEMA action items 5, 7 and 8:
  5 — the consolidated expiry digest lives in services/notifications and is
      driven by services/subscriptions; this module owns the records it reads.
  7 — Windows keys can be imported straight from endpoint snapshots rather
      than typed in one machine at a time.
  8 — Edit sits beside the software name, and each record takes a PO / licence
      document attachment.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import require_staff, require_admin
from ..database import get_db
from ..jinja_filters import install_on
from ..models import SoftwareSubscription, Tenant, UploadedFile, User
from ..services import subscriptions as subs_svc

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(templates)

router = APIRouter(tags=["subscriptions"])


def _ctx(user: User, db: Session, **extra) -> dict:
    tenant = db.query(Tenant).first()
    return {"current_user": user, "tenant": tenant, **extra}


def _parse_date(raw: str | None) -> date | None:
    """Read a <input type=date> value. Blank means 'not recorded', which is a
    real state here — a perpetual licence has no expiry — so it maps to None
    rather than being rejected."""
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _parse_int(raw: str | None) -> int | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        n = int(s)
    except ValueError:
        return None
    return n if n >= 0 else None


@router.get("/subscriptions", response_class=HTMLResponse)
def subscriptions_list(
    request: Request,
    q: str = "",
    flash: str = "",
    error: str = "",
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = subs_svc.list_subscriptions(db, user.tenant_id, q=q)
    today = subs_svc._today()

    def _download_url(s: SoftwareSubscription) -> str | None:
        """/files/<uuid><ext> — the stored name is the id plus the same safe
        extension the upload pipeline derived, so rebuild it the same way
        rather than guessing from the original filename in the template."""
        if not s.attachment_id or s.attachment is None:
            return None
        from ..api.uploads import _safe_ext
        return f"/files/{s.attachment_id}{_safe_ext(s.attachment.original_filename)}"

    view = [{
        "sub":     s,
        "status":  subs_svc.status_of(s, today),
        "days":    subs_svc.days_to_expiry(s, today),
        "download_url": _download_url(s),
    } for s in rows]
    return templates.TemplateResponse(
        request=request, name="subscriptions_list.html",
        context=_ctx(user, db,
                     rows=view, q=q, flash=flash, error=error,
                     kpis=subs_svc.kpis(db, user.tenant_id),
                     horizon=subs_svc.DEFAULT_HORIZON_DAYS),
    )


@router.post("/subscriptions/new")
async def subscription_create(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    name = (form.get("software_name") or "").strip()
    if not name:
        return RedirectResponse(url="/subscriptions?error=Software+name+is+required", status_code=303)

    sub = SoftwareSubscription(
        tenant_id=user.tenant_id,
        software_name=name[:200],
        vendor=((form.get("vendor") or "").strip() or None),
        license_key=((form.get("license_key") or "").strip() or None),
        seats=_parse_int(form.get("seats")),
        po_reference=((form.get("po_reference") or "").strip() or None),
        purchased_on=_parse_date(form.get("purchased_on")),
        starts_on=_parse_date(form.get("starts_on")),
        expires_on=_parse_date(form.get("expires_on")),
        notes=(form.get("notes") or "").strip(),
        created_by_id=user.id,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)

    await _maybe_attach(form, sub, user, db)
    return RedirectResponse(url=f"/subscriptions?flash=Added+{name[:60]}", status_code=303)


@router.post("/subscriptions/{sub_id}/edit")
async def subscription_edit(
    sub_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    sub = db.get(SoftwareSubscription, sub_id)
    if sub is None or sub.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    form = await request.form()
    name = (form.get("software_name") or "").strip()
    if not name:
        return RedirectResponse(url="/subscriptions?error=Software+name+is+required", status_code=303)

    sub.software_name = name[:200]
    sub.vendor       = (form.get("vendor") or "").strip() or None
    sub.license_key  = (form.get("license_key") or "").strip() or None
    sub.seats        = _parse_int(form.get("seats"))
    sub.po_reference = (form.get("po_reference") or "").strip() or None
    sub.purchased_on = _parse_date(form.get("purchased_on"))
    sub.starts_on    = _parse_date(form.get("starts_on"))
    sub.expires_on   = _parse_date(form.get("expires_on"))
    sub.notes        = (form.get("notes") or "").strip()
    db.commit()

    await _maybe_attach(form, sub, user, db)
    return RedirectResponse(url=f"/subscriptions?flash=Updated+{name[:60]}", status_code=303)


async def _maybe_attach(form, sub: SoftwareSubscription, user: User, db: Session) -> None:
    """Save an attached PO / licence document, if one came with the form.

    Reuses the UploadedFile pipeline so this does not become a second storage
    path with its own bugs. A replaced attachment leaves the old UploadedFile
    row alone: it may be referenced elsewhere, and orphan cleanup is a
    housekeeping job, not something to do inside a form post.
    """
    import hashlib
    import uuid as _uuid

    doc = form.get("attachment")
    if not (hasattr(doc, "filename") and doc.filename and getattr(doc, "size", 0)):
        return
    from ..api.uploads import ensure_upload_dir, _safe_ext, MAX_BYTES

    file_id = _uuid.uuid4().hex
    ext = _safe_ext(doc.filename)
    target = ensure_upload_dir() / f"{file_id}{ext}"
    h = hashlib.sha256()
    written = 0
    try:
        with open(target, "wb") as out:
            while True:
                chunk = await doc.read(1024 * 256)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_BYTES:
                    out.close()
                    target.unlink(missing_ok=True)
                    return
                h.update(chunk)
                out.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        return

    db.add(UploadedFile(
        id=file_id, tenant_id=user.tenant_id,
        original_filename=doc.filename[:255],
        content_type=(doc.content_type or "application/octet-stream")[:120],
        size_bytes=written, sha256=h.hexdigest(),
        purpose="license_doc", created_by_id=user.id,
    ))
    sub.attachment_id = file_id
    db.commit()


@router.post("/subscriptions/{sub_id}/delete")
def subscription_delete(
    sub_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    sub = db.get(SoftwareSubscription, sub_id)
    if sub is None or sub.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    name = sub.software_name
    db.delete(sub)
    db.commit()
    return RedirectResponse(url=f"/subscriptions?flash=Deleted+{name[:60]}", status_code=303)


@router.post("/subscriptions/import-windows")
def subscriptions_import_windows(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Seed subscription rows from the Windows OEM keys the agents already
    report (TEMA action item 7). Idempotent — safe to re-run as endpoints
    enrol."""
    result = subs_svc.import_windows_keys(db, user.tenant_id, created_by_id=user.id)
    msg = (f"Imported+{result['created']}+key(s);+{result['skipped_existing']}+already+present;+"
           f"{result['no_key']}+endpoint(s)+report+no+OEM+key")
    return RedirectResponse(url=f"/subscriptions?flash={msg}", status_code=303)


@router.get("/subscriptions/windows-keys", response_class=HTMLResponse)
def subscriptions_windows_keys(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """What each endpoint reports, including the ones with no recoverable key —
    those are the machines that still need a key entering by hand."""
    return templates.TemplateResponse(
        request=request, name="subscriptions_windows_keys.html",
        context=_ctx(user, db, rows=subs_svc.discover_windows_keys(db, user.tenant_id)),
    )


@router.get("/subscriptions/export.csv")
def subscriptions_export(
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    csv_text = subs_svc.export_csv(db, user.tenant_id)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="octoassist-subscriptions.csv"'},
    )
