"""Outbound notifications — fire-and-forget around domain events.

Every public function here:
  - Catches all exceptions; never blocks the caller.
  - Decides whether to send (e.g. only if mail is configured, only to a
    real recipient).
  - Composes a short subject + plain-text body. HTML is intentionally
    avoided — keeps the templates trivial and renders cleanly in any
    Microsoft mailbox.

If you need synchronous send (e.g. user clicked "Send test"), call
services.email.send_email directly.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from sqlalchemy.orm import Session

from .email import EmailError, send_email
from ..models import (
    Change, PatchWindow, Tenant, Ticket, TicketComment, TicketEventKind, User,
)

log = logging.getLogger("octoassist.notify")


def _mail_configured(tenant: Tenant) -> bool:
    p = (tenant.mail_provider or "smtp").lower()
    if p == "graph":
        return bool(tenant.graph_tenant_id and tenant.graph_client_id and tenant.graph_client_secret and tenant.graph_from)
    return bool(tenant.smtp_host and tenant.smtp_port and tenant.smtp_from)


def _fire(tenant: Tenant, to: str, subject: str, body: str) -> None:
    """Send in a background thread — never blocks the request thread."""
    if not _mail_configured(tenant):
        return
    if not to:
        return

    def _go():
        try:
            send_email(tenant=tenant, to=to, subject=subject, body_text=body)
            log.info("notify sent: %r → %s", subject, to)
        except EmailError as e:
            log.warning("notify failed: %r → %s: %s", subject, to, e)
        except Exception as e:  # noqa: BLE001
            log.exception("notify crashed: %r → %s: %s", subject, to, e)

    threading.Thread(target=_go, daemon=True).start()


def _cab_emails(db: Session, tenant_id: int, exclude: set[str] | None = None) -> list[str]:
    """Return active CAB-member emails for the tenant. The exclude set prevents
    sending duplicate copies to people already on the primary recipient list."""
    rows = (db.query(User.email)
              .filter(User.tenant_id == tenant_id,
                      User.is_cab_member.is_(True),
                      User.is_active.is_(True),
                      User.email.is_not(None))
              .all())
    skip = exclude or set()
    out = []
    for (e,) in rows:
        if e and e not in skip:
            out.append(e)
            skip.add(e)
    return out


# ---------- Tickets ----------

def ticket_created(db: Session, ticket: Ticket) -> None:
    tenant = ticket.tenant
    if tenant is None or not _mail_configured(tenant):
        return
    base_url = ""  # Future: derive from request or settings
    body = (
        f"New ticket: {ticket.ticket_number}\n"
        f"Title:   {ticket.title}\n"
        f"Type:    {ticket.kind.value}\n"
        f"Priority: {ticket.priority.value}\n"
        f"Reporter: {ticket.reporter.display_name if ticket.reporter else '—'}\n"
        f"\n{ticket.description or '(no description)'}\n"
    )
    sent: set[str] = set()
    if ticket.reporter and ticket.reporter.email:
        _fire(tenant, ticket.reporter.email,
              f"[OctoAssist] {ticket.ticket_number} — created",
              body + f"\n— You are the reporter on this ticket.\n")
        sent.add(ticket.reporter.email)
    if ticket.assignee and ticket.assignee.email and ticket.assignee_id != ticket.reporter_id:
        _fire(tenant, ticket.assignee.email,
              f"[OctoAssist] {ticket.ticket_number} — assigned to you",
              body + f"\n— You have been assigned this ticket.\n")
        sent.add(ticket.assignee.email)
    # Phase J: CAB members get FYI on every new ticket
    for cab_email in _cab_emails(db, ticket.tenant_id, exclude=sent):
        _fire(tenant, cab_email,
              f"[OctoAssist · CAB] {ticket.ticket_number} — new {ticket.kind.value.replace('_',' ')}",
              body + f"\n— You receive this as a CAB member.\n")


def ticket_status_changed(db: Session, ticket: Ticket, old_status: str) -> None:
    tenant = ticket.tenant
    if tenant is None or not _mail_configured(tenant):
        return
    body = (
        f"{ticket.ticket_number}: {old_status} → {ticket.status.value}\n"
        f"Title: {ticket.title}\n"
    )
    if ticket.reporter and ticket.reporter.email:
        _fire(tenant, ticket.reporter.email,
              f"[OctoAssist] {ticket.ticket_number} — {ticket.status.value.replace('_',' ')}",
              body)


def ticket_comment(db: Session, ticket: Ticket, comment: TicketComment) -> None:
    if comment.is_internal:
        return  # internal notes don't notify the requester
    tenant = ticket.tenant
    if tenant is None or not _mail_configured(tenant):
        return
    author_email = comment.author.email if comment.author else ""
    body = (
        f"{ticket.ticket_number}: new comment\n"
        f"Title:  {ticket.title}\n"
        f"Author: {comment.author.display_name if comment.author else '—'}\n"
        f"\n{comment.body}\n"
    )
    # Notify the OTHER party (so the author doesn't get an email of their own comment).
    recipients = []
    if ticket.reporter and ticket.reporter.email and ticket.reporter.email != author_email:
        recipients.append(ticket.reporter.email)
    if ticket.assignee and ticket.assignee.email and ticket.assignee.email != author_email \
            and (ticket.assignee.email not in recipients):
        recipients.append(ticket.assignee.email)
    for to in recipients:
        _fire(tenant, to,
              f"[OctoAssist] {ticket.ticket_number} — new comment",
              body)


# ---------- Patch windows ----------

def patch_window_started(db: Session, window: PatchWindow) -> None:
    tenant = window.tenant if hasattr(window, "tenant") else db.get(Tenant, window.tenant_id)
    if tenant is None or not _mail_configured(tenant):
        return
    if not tenant.notification_email:
        return
    n_targets = len(window.targets)
    n_pkgs = len(window.selected_packages or [])
    body = (
        f"Patch window started: {window.name}\n"
        f"Targets: {n_targets} endpoint{'s' if n_targets != 1 else ''}\n"
        f"Approved packages: {n_pkgs}\n"
        f"Auto-execute: {'on' if window.auto_execute else 'off (manual tracking)'}\n"
        f"\n{window.description or ''}\n"
    )
    _fire(tenant, tenant.notification_email,
          f"[OctoAssist] Patch window started — {window.name}",
          body)


def patch_window_completed(db: Session, window: PatchWindow) -> None:
    tenant = window.tenant if hasattr(window, "tenant") else db.get(Tenant, window.tenant_id)
    if tenant is None or not _mail_configured(tenant):
        return
    if not tenant.notification_email:
        return
    counts = {"succeeded": 0, "failed": 0, "skipped": 0, "in_progress": 0, "planned": 0}
    for t in window.targets:
        counts[t.status.value] = counts.get(t.status.value, 0) + 1
    body = (
        f"Patch window completed: {window.name}\n"
        f"Targets: {len(window.targets)}\n"
        f"  succeeded: {counts['succeeded']}\n"
        f"  failed:    {counts['failed']}\n"
        f"  skipped:   {counts['skipped']}\n"
    )
    _fire(tenant, tenant.notification_email,
          f"[OctoAssist] Patch window completed — {window.name}",
          body)


# ---------- Changes ----------

def change_submitted(db: Session, change: Change) -> None:
    tenant = change.tenant if hasattr(change, "tenant") else db.get(Tenant, change.tenant_id)
    if tenant is None or not _mail_configured(tenant):
        return
    body = (
        f"Change submitted for CAB review: {change.change_number}\n"
        f"Title: {change.title}\n"
        f"Type: {change.change_type.value}, Risk: {change.risk.value}\n"
        f"Requester: {change.requester.display_name if change.requester else '—'}\n"
        f"\n{change.description or ''}\n"
    )
    sent: set[str] = set()
    if tenant.notification_email:
        _fire(tenant, tenant.notification_email,
              f"[OctoAssist] {change.change_number} — under CAB review",
              body)
        sent.add(tenant.notification_email)
    # Phase J: every CAB member gets the change for review
    for cab_email in _cab_emails(db, change.tenant_id, exclude=sent):
        _fire(tenant, cab_email,
              f"[OctoAssist · CAB] {change.change_number} — needs your review",
              body + f"\n— You receive this as a CAB member. Approve or reject at /changes/{change.id}.\n")
