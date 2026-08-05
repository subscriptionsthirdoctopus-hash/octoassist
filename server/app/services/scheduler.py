"""Background scheduler for patch windows.

A single daemon thread wakes every TICK seconds and:
  - Promotes patch windows whose status='scheduled' AND scheduled_for <= now
    to status='in_progress'.

Agents poll for pending deployments on their next check-in, so the
"actual" execution lag is min(TICK, agent check-in interval).

Started from main.py @app.on_event("startup"). Stops on process exit
(daemon=True). For Phase 9 this can be replaced with a proper job
queue / APScheduler if needed.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from .patches import transition_window
from ..database import SessionLocal
from ..models import PatchWindow, PatchWindowStatus

log = logging.getLogger("octoassist.scheduler")

TICK_SECONDS = 60
_STARTED = False
_LOCK = threading.Lock()
_LAST_DIGEST_SENT_DATE = None
_LAST_EXPIRY_DIGEST_DATE = None



def _due_windows(db) -> list[PatchWindow]:
    now = datetime.now(timezone.utc)
    return (db.query(PatchWindow)
              .filter(PatchWindow.status == PatchWindowStatus.scheduled,
                      PatchWindow.scheduled_for.isnot(None),
                      PatchWindow.scheduled_for <= now)
              .all())


def _tick_once() -> int:
    db = SessionLocal()
    try:
        # Trigger daily admin audit digest around 18:00 UTC daily (23:30 IST, end of the day)
        global _LAST_DIGEST_SENT_DATE
        now_utc = datetime.now(timezone.utc)
        if now_utc.hour == 18 and now_utc.minute >= 0:
            current_date_str = now_utc.strftime("%Y-%m-%d")
            if _LAST_DIGEST_SENT_DATE != current_date_str:
                from .notifications import send_daily_admin_audit_digest
                try:
                    log.info("Triggering daily admin audit digest for date %s", current_date_str)
                    send_daily_admin_audit_digest(db)
                    _LAST_DIGEST_SENT_DATE = current_date_str
                except Exception as e:
                    log.exception("Failed to send daily admin audit digest: %s", e)

        # Consolidated software-expiry digest — one mail per tenant per day
        # (TEMA action item 5). 03:30 UTC is 09:00 IST, so it lands at the top
        # of the working day rather than overnight. Guarded by its own date
        # marker so a restart inside the window cannot send it twice.
        global _LAST_EXPIRY_DIGEST_DATE
        if now_utc.hour == 3 and now_utc.minute >= 30:
            expiry_date_str = now_utc.strftime("%Y-%m-%d")
            if _LAST_EXPIRY_DIGEST_DATE != expiry_date_str:
                from .notifications import send_software_expiry_digest
                try:
                    n = send_software_expiry_digest(db)
                    log.info("Software expiry digest sent to %d tenant(s) for %s",
                             n, expiry_date_str)
                    _LAST_EXPIRY_DIGEST_DATE = expiry_date_str
                except Exception as e:
                    log.exception("Failed to send software expiry digest: %s", e)

        windows = _due_windows(db)
        promoted = 0
        for w in windows:
            log.info("Promoting window id=%s name=%r scheduled_for=%s → in_progress",
                     w.id, w.name, w.scheduled_for)
            transition_window(db, window=w, new_status=PatchWindowStatus.in_progress)
            promoted += 1
        return promoted
    finally:
        db.close()



def _run() -> None:
    log.info("scheduler loop start (tick=%ds)", TICK_SECONDS)
    while True:
        try:
            n = _tick_once()
            if n:
                log.info("scheduler promoted %d window(s) this tick", n)
        except Exception as e:  # noqa: BLE001
            log.exception("scheduler tick errored: %s", e)
        time.sleep(TICK_SECONDS)


def start_in_background() -> None:
    """Idempotent — only one scheduler thread per process."""
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        t = threading.Thread(target=_run, name="patch-scheduler", daemon=True)
        t.start()
        _STARTED = True
        log.info("Patch scheduler thread started (daemon)")
