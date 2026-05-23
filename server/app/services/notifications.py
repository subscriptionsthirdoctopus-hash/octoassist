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

# ---------- Tickets ----------

def ticket_created(db: Session, ticket: Ticket) -> None:
    tenant = ticket.tenant
    if tenant is None or not _mail_configured(tenant):
        return
    from ..config import settings
    base_url = settings.base_url.rstrip("/")
    body = (
        f"New ticket: {ticket.ticket_number}\n"
        f"Title:   {ticket.title}\n"
        f"Type:    {ticket.kind.value}\n"
        f"Priority: {ticket.priority.value}\n"
        f"Category: {ticket.category.name if ticket.category else 'Others'}\n"
        f"Reporter: {ticket.reporter.display_name if ticket.reporter else '—'}\n"
        f"Location: {ticket.location or 'Not Specified'}\n"
        f"\n{ticket.description or '(no description)'}\n"
    )
    sent: set[str] = set()
    if ticket.reporter and ticket.reporter.email:
        from ..models import UserRole
        path = "/portal/ticket" if ticket.reporter.role == UserRole.requester else "/tickets"
        _fire(tenant, ticket.reporter.email,
              f"[OctoAssist] {ticket.ticket_number} — created",
              body + f"\n— You are the reporter on this ticket.\nView ticket: {base_url}{path}/{ticket.id}\n")
        sent.add(ticket.reporter.email)
    if ticket.assignee and ticket.assignee.email and ticket.assignee_id != ticket.reporter_id:
        from ..models import UserRole
        path = "/portal/ticket" if ticket.assignee.role == UserRole.requester else "/tickets"
        _fire(tenant, ticket.assignee.email,
              f"[OctoAssist] {ticket.ticket_number} — assigned to you",
              body + f"\n— You have been assigned this ticket.\nView ticket: {base_url}{path}/{ticket.id}\n")
        sent.add(ticket.assignee.email)
    # Phase J: CAB members get FYI on every new ticket
    for cab_email in _cab_emails(db, ticket.tenant_id, exclude=sent):
        _fire(tenant, cab_email,
              f"[OctoAssist · CAB] {ticket.ticket_number} — new {ticket.kind.value.replace('_',' ')}",
              body + f"\n— You receive this as a CAB member.\nView ticket: {base_url}/tickets/{ticket.id}\n")


def ticket_status_changed(db: Session, ticket: Ticket, old_status: str) -> None:
    tenant = ticket.tenant
    if tenant is None or not _mail_configured(tenant):
        return
    from ..config import settings
    base_url = settings.base_url.rstrip("/")
    if ticket.reporter and ticket.reporter.email:
        from ..models import UserRole
        path = "/portal/ticket" if ticket.reporter.role == UserRole.requester else "/tickets"
        body = (
            f"Ticket {ticket.ticket_number} status has been updated.\n"
            f"Title: {ticket.title}\n"
            f"Change: {old_status.replace('_',' ').upper()} → {ticket.status.value.replace('_',' ').upper()}\n"
            f"\nView updated ticket: {base_url}{path}/{ticket.id}\n"
        )
        _fire(tenant, ticket.reporter.email,
              f"[OctoAssist] {ticket.ticket_number} — Status Updated to {ticket.status.value.replace('_',' ').title()}",
              body)


def ticket_comment(db: Session, ticket: Ticket, comment: TicketComment) -> None:
    if comment.is_internal:
        return  # internal notes don't notify the requester
    tenant = ticket.tenant
    if tenant is None or not _mail_configured(tenant):
        return
    from ..config import settings
    base_url = settings.base_url.rstrip("/")
    author_email = comment.author.email if comment.author else ""
    
    # Notify the OTHER party (so the author doesn't get an email of their own comment).
    if ticket.reporter and ticket.reporter.email and ticket.reporter.email != author_email:
        from ..models import UserRole
        path = "/portal/ticket" if ticket.reporter.role == UserRole.requester else "/tickets"
        body = (
            f"New comment on ticket {ticket.ticket_number}:\n"
            f"Title:  {ticket.title}\n"
            f"Author: {comment.author.display_name if comment.author else '—'}\n"
            f"\n{comment.body}\n"
            f"\nView ticket and reply: {base_url}{path}/{ticket.id}\n"
        )
        _fire(tenant, ticket.reporter.email,
              f"[OctoAssist] {ticket.ticket_number} — New comment posted",
              body)

    if ticket.assignee and ticket.assignee.email and ticket.assignee.email != author_email:
        from ..models import UserRole
        path = "/portal/ticket" if ticket.assignee.role == UserRole.requester else "/tickets"
        body = (
            f"New comment on ticket {ticket.ticket_number}:\n"
            f"Title:  {ticket.title}\n"
            f"Author: {comment.author.display_name if comment.author else '—'}\n"
            f"\n{comment.body}\n"
            f"\nView ticket and reply: {base_url}{path}/{ticket.id}\n"
        )
        _fire(tenant, ticket.assignee.email,
              f"[OctoAssist] {ticket.ticket_number} — New comment posted",
              body)


def ticket_assigned(db: Session, ticket: Ticket, old_assignee_id: int | None) -> None:
    tenant = ticket.tenant
    if tenant is None or not _mail_configured(tenant):
        return
    from ..config import settings
    base_url = settings.base_url.rstrip("/")
    
    # Notify reporter
    if ticket.reporter and ticket.reporter.email:
        from ..models import UserRole
        path = "/portal/ticket" if ticket.reporter.role == UserRole.requester else "/tickets"
        assignee_name = ticket.assignee.display_name if ticket.assignee else "Unassigned"
        body = (
            f"Ticket {ticket.ticket_number} has been assigned to a technician.\n"
            f"Title: {ticket.title}\n"
            f"Technician: {assignee_name}\n"
            f"\nView ticket details: {base_url}{path}/{ticket.id}\n"
        )
        _fire(tenant, ticket.reporter.email,
              f"[OctoAssist] {ticket.ticket_number} — Assigned to Technician",
              body)

    # Notify new assignee
    if ticket.assignee and ticket.assignee.email and ticket.assignee_id != ticket.reporter_id:
        from ..models import UserRole
        path = "/portal/ticket" if ticket.assignee.role == UserRole.requester else "/tickets"
        body = (
            f"Ticket {ticket.ticket_number} has been assigned to you.\n"
            f"Title: {ticket.title}\n"
            f"Priority: {ticket.priority.value}\n"
            f"Reporter: {ticket.reporter.display_name if ticket.reporter else '—'}\n"
            f"\n{ticket.description or '(no description)'}\n"
            f"\nView ticket details: {base_url}{path}/{ticket.id}\n"
        )
        _fire(tenant, ticket.assignee.email,
              f"[OctoAssist] {ticket.ticket_number} — assigned to you",
              body)


def ticket_priority_changed(db: Session, ticket: Ticket, old_priority: str) -> None:
    tenant = ticket.tenant
    if tenant is None or not _mail_configured(tenant):
        return
    from ..config import settings
    base_url = settings.base_url.rstrip("/")
    
    # Notify reporter
    if ticket.reporter and ticket.reporter.email:
        from ..models import UserRole
        path = "/portal/ticket" if ticket.reporter.role == UserRole.requester else "/tickets"
        body = (
            f"Ticket {ticket.ticket_number} priority has been updated.\n"
            f"Title: {ticket.title}\n"
            f"Change: {old_priority.upper()} → {ticket.priority.value.upper()}\n"
            f"\nView ticket details: {base_url}{path}/{ticket.id}\n"
        )
        _fire(tenant, ticket.reporter.email,
              f"[OctoAssist] {ticket.ticket_number} — Priority Updated to {ticket.priority.value.title()}",
              body)


# ---------- Patch windows ----------

def patch_window_started(db: Session, window: PatchWindow) -> None:
    tenant = window.tenant if hasattr(window, "tenant") else db.get(Tenant, window.tenant_id)
    if tenant is None or not _mail_configured(tenant):
        return
    if not tenant.notification_email:
        return
    from ..config import settings
    base_url = settings.base_url.rstrip("/")
    n_targets = len(window.targets)
    n_pkgs = len(window.selected_packages or [])
    body = (
        f"Patch window started: {window.name}\n"
        f"Targets: {n_targets} endpoint{'s' if n_targets != 1 else ''}\n"
        f"Approved packages: {n_pkgs}\n"
        f"Auto-execute: {'on' if window.auto_execute else 'off (manual tracking)'}\n"
        f"\n{window.description or ''}\n"
        f"\nView patch deployment portal: {base_url}/patches\n"
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
    from ..config import settings
    base_url = settings.base_url.rstrip("/")
    counts = {"succeeded": 0, "failed": 0, "skipped": 0, "in_progress": 0, "planned": 0}
    for t in window.targets:
        counts[t.status.value] = counts.get(t.status.value, 0) + 1
    body = (
        f"Patch window completed: {window.name}\n"
        f"Targets: {len(window.targets)}\n"
        f"  succeeded: {counts['succeeded']}\n"
        f"  failed:    {counts['failed']}\n"
        f"  skipped:   {counts['skipped']}\n"
        f"\nView complete results: {base_url}/patches\n"
    )
    _fire(tenant, tenant.notification_email,
          f"[OctoAssist] Patch window completed — {window.name}",
          body)


# ---------- Changes ----------

def change_submitted(db: Session, change: Change) -> None:
    tenant = change.tenant if hasattr(change, "tenant") else db.get(Tenant, change.tenant_id)
    if tenant is None or not _mail_configured(tenant):
        return
    from ..config import settings
    base_url = settings.base_url.rstrip("/")
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
              body + f"\nView details: {base_url}/changes/{change.id}\n")
        sent.add(tenant.notification_email)
    # Phase J: every CAB member gets the change for review
    for cab_email in _cab_emails(db, change.tenant_id, exclude=sent):
        _fire(tenant, cab_email,
              f"[OctoAssist · CAB] {change.change_number} — needs your review",
              body + f"\n— You receive this as a CAB member.\nApprove or reject at: {base_url}/changes/{change.id}\n")
