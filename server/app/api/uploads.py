"""File upload + download.

  POST /api/v1/uploads        — admin uploads an installer / wallpaper / script
                                via multipart. Returns {id, url, size, sha256}.
                                Session-auth (require_admin).
  GET  /files/{uuid}.{ext}    — public download. UUID is 128 bits of entropy,
                                only ever appears inside a tenant-scoped
                                RemoteAction row — effectively unguessable.

Storage: filesystem under UPLOAD_DIR. The path is host-mounted via Docker
volume so files survive container rebuilds.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from ..auth import require_admin
from ..database import get_db
from ..models import Tenant, UploadedFile, User

UPLOAD_DIR = Path(os.environ.get("OCTOASSIST_UPLOAD_DIR",
                                 "/srv/octoassist/uploads"))
MAX_BYTES = 1024 * 1024 * 1024  # 1 GB cap per upload


def ensure_upload_dir() -> Path:
    """Return UPLOAD_DIR, creating it on first write.

    Deliberately not done at import time. UPLOAD_DIR defaults to a path the
    production container owns, so creating it on import made merely importing
    the app a privileged filesystem write — it raised PermissionError anywhere
    that path is not writable (CI, a developer laptop) and, when the process
    did happen to be root, silently created /srv/octoassist on a machine that
    had no business having it.

    mkdir is idempotent and costs a stat, so calling it per upload is cheap.
    Reads (serve_file) do not call this: serving a file never needs to create
    the directory, and a missing directory there is already a 404.
    """
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOAD_DIR


router = APIRouter(tags=["uploads"])


def _safe_ext(name: str) -> str:
    """Return a safe extension (alnum-only, lowercased, max 8 chars)."""
    _, ext = os.path.splitext(name or "")
    ext = "".join(c for c in (ext or "").lower() if c.isalnum() or c == ".")
    return ext[:9] if ext.startswith(".") else (("." + ext[:8]) if ext else ".bin")


@router.post("/api/v1/uploads")
async def upload_file(
    file: UploadFile = File(...),
    purpose: str = Form("installer"),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Save the uploaded multipart file. Returns the URL the agent will fetch."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")
    if purpose not in ("installer", "wallpaper", "script"):
        purpose = "installer"

    file_id = uuid.uuid4().hex
    ext = _safe_ext(file.filename)
    try:
        target = ensure_upload_dir() / f"{file_id}{ext}"
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"upload directory unavailable: {e}")

    h = hashlib.sha256()
    written = 0
    try:
        with open(target, "wb") as out:
            while True:
                chunk = await file.read(1024 * 256)   # 256 KB
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_BYTES:
                    out.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail=f"File exceeds {MAX_BYTES // (1024*1024)} MB cap")
                h.update(chunk)
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"upload failed: {e}")

    row = UploadedFile(
        id=file_id,
        tenant_id=user.tenant_id,
        original_filename=file.filename[:255],
        content_type=(file.content_type or "application/octet-stream")[:120],
        size_bytes=written,
        sha256=h.hexdigest(),
        purpose=purpose,
        created_by_id=user.id,
    )
    db.add(row)
    db.commit()

    # The URL agents fetch. Path uses the same extension we stored.
    return JSONResponse({
        "id":   file_id,
        "url":  f"/files/{file_id}{ext}",
        "size_bytes": written,
        "sha256": row.sha256,
        "original_filename": row.original_filename,
    })


@router.get("/files/{name}")
def serve_file(name: str, db: Session = Depends(get_db)):
    """Public download route. UUID-in-path is the access control."""
    # name format: <uuid>.<ext>
    base, _ = os.path.splitext(name)
    if len(base) != 32 or not all(c in "0123456789abcdef" for c in base):
        raise HTTPException(status_code=404)
    row = db.get(UploadedFile, base)
    if row is None:
        raise HTTPException(status_code=404)
    path = UPLOAD_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="file missing on disk")
    return FileResponse(
        str(path),
        media_type=row.content_type or "application/octet-stream",
        filename=row.original_filename,
    )
