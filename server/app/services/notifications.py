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

def _render_ticket_details(ticket: Ticket) -> str:
    kind_str = "Incident" if ticket.kind.value == "incident" else "Service Request"
    assignee_str = f"{ticket.assignee.display_name} ({ticket.assignee.email})" if ticket.assignee else "Not Assigned (IT Operations Queue)"
    reporter_str = f"{ticket.reporter.display_name} ({ticket.reporter.email})" if ticket.reporter else "—"
    if ticket.created_at:
        from datetime import timezone, timedelta
        IST = timezone(timedelta(hours=5, minutes=30), name="IST")
        dt = ticket.created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        created_str = dt.astimezone(IST).strftime("%d-%b-%Y %I:%M %p IST")
    else:
        created_str = "—"

    
    return (
        "============================================================\n"
        "                OCTOASSIST ITSM NOTIFICATION                \n"
        "============================================================\n\n"
        f"Ticket Number:   {ticket.ticket_number}\n"
        f"Subject/Title:   {ticket.title}\n"
        f"Classification:  {kind_str}\n"
        f"Current Status:  {ticket.status.value.replace('_', ' ').upper()}\n"
        "\n"
        "------------------------- TELEMETRY -------------------------\n"
        f"Priority Level:  {ticket.priority.value.upper()}\n"
        f"Category Area:   {ticket.category.name if ticket.category else 'Others'}\n"
        f"Reporter Name:   {reporter_str}\n"
        f"Office Location: {ticket.location or 'Not Specified'}\n"
        f"Assigned To:     {assignee_str}\n"
        f"Logged Date:     {created_str}\n"
        "\n"
        "----------------------- DESCRIPTION -----------------------\n"
        f"{ticket.description or '(No description provided)'}\n"
        "\n"
        "============================================================\n"
    )


def ticket_created(db: Session, ticket: Ticket) -> None:
    tenant = ticket.tenant
    if tenant is None or not _mail_configured(tenant):
        return
    prefs = tenant.notification_settings or {}
    from ..config import settings
    base_url = settings.base_url.rstrip("/")
    details = _render_ticket_details(ticket)
    
    sent: set[str] = set()
    if prefs.get("notify_ticket_created_requester", True) and ticket.reporter and ticket.reporter.email:
        from ..models import UserRole
        path = "/portal/ticket" if ticket.reporter.role == UserRole.requester else "/tickets"
        body = (
            f"Hello {ticket.reporter.display_name},\n\n"
            f"Your ticket {ticket.ticket_number} has been logged in the system.\n\n"
            f"{details}\n"
            f"To view comment history, reply to this ticket, or attach files,\n"
            f"please visit your customer portal at the secure link below:\n"
            f"{base_url}{path}/{ticket.id}\n\n"
            "Best regards,\n"
            "Tema IT Team\n"
        )
        _fire(tenant, ticket.reporter.email,
              f"[OctoAssist] {ticket.ticket_number} — created",
              body)
        sent.add(ticket.reporter.email)
    
    if prefs.get("notify_ticket_created_assignee", True) and ticket.assignee and ticket.assignee.email and ticket.assignee_id != ticket.reporter_id:
        from ..models import UserRole
        path = "/portal/ticket" if ticket.assignee.role == UserRole.requester else "/tickets"
        body = (
            f"Hello {ticket.assignee.display_name},\n\n"
            f"A new ticket {ticket.ticket_number} has been assigned to you.\n\n"
            f"{details}\n"
            f"To view details and begin work on this ticket, click the link below:\n"
            f"{base_url}{path}/{ticket.id}\n\n"
            "Best regards,\n"
            "OctoAssist System Service\n"
        )
        _fire(tenant, ticket.assignee.email,
              f"[OctoAssist] {ticket.ticket_number} — assigned to you",
              body)
        sent.add(ticket.assignee.email)
        
    if prefs.get("notify_ticket_created_cab", True):
        for cab_email in _cab_emails(db, ticket.tenant_id, exclude=sent):
            body = (
                f"Hello,\n\n"
                f"A new ticket {ticket.ticket_number} has been logged in the queue and is under review.\n\n"
                f"{details}\n"
                f"To view the ticket details and track progress, click the link below:\n"
                f"{base_url}/tickets/{ticket.id}\n\n"
                "Best regards,\n"
                "OctoAssist System Service\n"
            )
            _fire(tenant, cab_email,
                  f"[OctoAssist · CAB] {ticket.ticket_number} — new {ticket.kind.value.replace('_',' ')}",
                  body)


def ticket_status_changed(db: Session, ticket: Ticket, old_status: str) -> None:
    tenant = ticket.tenant
    if tenant is None or not _mail_configured(tenant):
        return
    prefs = tenant.notification_settings or {}
    from ..config import settings
    base_url = settings.base_url.rstrip("/")
    details = _render_ticket_details(ticket)
    
    if prefs.get("notify_status_changed_requester", True) and ticket.reporter and ticket.reporter.email:
        from ..models import UserRole
        path = "/portal/ticket" if ticket.reporter.role == UserRole.requester else "/tickets"
        body = (
            f"Hello {ticket.reporter.display_name},\n\n"
            f"The status of your ticket {ticket.ticket_number} has been updated from "
            f"\"{old_status.replace('_',' ').upper()}\" to \"{ticket.status.value.replace('_',' ').upper()}\".\n\n"
            f"{details}\n"
            f"To view comments or add replies, visit your customer portal:\n"
            f"{base_url}{path}/{ticket.id}\n\n"
            "Best regards,\n"
            "Tema IT Team\n"
        )
        _fire(tenant, ticket.reporter.email,
              f"[OctoAssist] {ticket.ticket_number} — Status Updated to {ticket.status.value.replace('_',' ').title()}",
              body)

    if prefs.get("notify_status_changed_assignee", True) and ticket.assignee and ticket.assignee.email and ticket.assignee_id != ticket.reporter_id:
        from ..models import UserRole
        path = "/portal/ticket" if ticket.assignee.role == UserRole.requester else "/tickets"
        body = (
            f"Hello {ticket.assignee.display_name},\n\n"
            f"The status of ticket {ticket.ticket_number} assigned to you has been updated from "
            f"\"{old_status.replace('_',' ').upper()}\" to \"{ticket.status.value.replace('_',' ').upper()}\".\n\n"
            f"{details}\n"
            f"To view details and continue work, click the link below:\n"
            f"{base_url}{path}/{ticket.id}\n\n"
            "Best regards,\n"
            "OctoAssist System Service\n"
        )
        _fire(tenant, ticket.assignee.email,
              f"[OctoAssist] {ticket.ticket_number} — Status Updated to {ticket.status.value.replace('_',' ').title()}",
              body)

    # Notify global IT operations notification email (technicians group) when ticket is closed or resolved
    if ticket.status.value in ("closed", "resolved") and tenant.notification_email:
        body = (
            f"Hello Team,\n\n"
            f"Ticket {ticket.ticket_number} has been updated to {ticket.status.value.upper()}.\n\n"
            f"{details}\n"
            f"To view details, click below:\n"
            f"{base_url}/tickets/{ticket.id}\n\n"
            "Best regards,\n"
            "OctoAssist System Service\n"
        )
        _fire(tenant, tenant.notification_email,
              f"[OctoAssist] {ticket.ticket_number} — Status Updated to {ticket.status.value.replace('_',' ').title()}",
              body)



def ticket_comment(db: Session, ticket: Ticket, comment: TicketComment) -> None:
    if comment.is_internal:
        return  # internal notes don't notify the requester
    tenant = ticket.tenant
    if tenant is None or not _mail_configured(tenant):
        return
    prefs = tenant.notification_settings or {}
    from ..config import settings
    base_url = settings.base_url.rstrip("/")
    author_email = comment.author.email if comment.author else ""
    details = _render_ticket_details(ticket)
    
    comment_box = (
        "--------------------- NEW COMMENT DETAIL ---------------------\n"
        f"Posted By: {comment.author.display_name if comment.author else 'System'}\n\n"
        f"{comment.body}\n"
        "--------------------------------------------------------------\n"
    )
    
    if prefs.get("notify_comment_added_requester", True) and ticket.reporter and ticket.reporter.email and ticket.reporter.email != author_email:
        from ..models import UserRole
        path = "/portal/ticket" if ticket.reporter.role == UserRole.requester else "/tickets"
        body = (
            f"Hello {ticket.reporter.display_name},\n\n"
            f"A new comment has been posted on your ticket {ticket.ticket_number}.\n\n"
            f"{comment_box}\n"
            f"{details}\n"
            f"To reply to this comment or upload attachments, click below:\n"
            f"{base_url}{path}/{ticket.id}\n\n"
            "Best regards,\n"
            "Tema IT Team\n"
        )
        _fire(tenant, ticket.reporter.email,
              f"[OctoAssist] {ticket.ticket_number} — New comment posted",
              body)

    if prefs.get("notify_comment_added_assignee", True) and ticket.assignee and ticket.assignee.email and ticket.assignee.email != author_email:
        from ..models import UserRole
        path = "/portal/ticket" if ticket.assignee.role == UserRole.requester else "/tickets"
        body = (
            f"Hello {ticket.assignee.display_name},\n\n"
            f"A new comment has been posted on ticket {ticket.ticket_number} assigned to you.\n\n"
            f"{comment_box}\n"
            f"{details}\n"
            f"To view and respond to this comment, click below:\n"
            f"{base_url}{path}/{ticket.id}\n\n"
            "Best regards,\n"
            "OctoAssist System Service\n"
        )
        _fire(tenant, ticket.assignee.email,
              f"[OctoAssist] {ticket.ticket_number} — New comment posted",
              body)


def ticket_assigned(db: Session, ticket: Ticket, old_assignee_id: int | None) -> None:
    tenant = ticket.tenant
    if tenant is None or not _mail_configured(tenant):
        return
    prefs = tenant.notification_settings or {}
    from ..config import settings
    base_url = settings.base_url.rstrip("/")
    details = _render_ticket_details(ticket)
    assignee_name = ticket.assignee.display_name if ticket.assignee else "Unassigned"
    
    # Notify reporter
    if prefs.get("notify_assigned_requester", True) and ticket.reporter and ticket.reporter.email:
        from ..models import UserRole
        path = "/portal/ticket" if ticket.reporter.role == UserRole.requester else "/tickets"
        body = (
            f"Hello {ticket.reporter.display_name},\n\n"
            f"Your ticket {ticket.ticket_number} has been assigned to IT technician {assignee_name}.\n\n"
            f"{details}\n"
            f"To track ticket progress or add notes, click below:\n"
            f"{base_url}{path}/{ticket.id}\n\n"
            "Best regards,\n"
            "Tema IT Team\n"
        )
        _fire(tenant, ticket.reporter.email,
              f"[OctoAssist] {ticket.ticket_number} — Assigned to Technician",
              body)

    # Notify new assignee
    if prefs.get("notify_assigned_assignee", True) and ticket.assignee and ticket.assignee.email and ticket.assignee_id != ticket.reporter_id:
        from ..models import UserRole
        path = "/portal/ticket" if ticket.assignee.role == UserRole.requester else "/tickets"
        body = (
            f"Hello {ticket.assignee.display_name},\n\n"
            f"Ticket {ticket.ticket_number} has been assigned to you.\n\n"
            f"{details}\n"
            f"To view details and begin work on this ticket, click below:\n"
            f"{base_url}{path}/{ticket.id}\n\n"
            "Best regards,\n"
            "OctoAssist System Service\n"
        )
        _fire(tenant, ticket.assignee.email,
              f"[OctoAssist] {ticket.ticket_number} — assigned to you",
              body)


def ticket_priority_changed(db: Session, ticket: Ticket, old_priority: str) -> None:
    tenant = ticket.tenant
    if tenant is None or not _mail_configured(tenant):
        return
    prefs = tenant.notification_settings or {}
    from ..config import settings
    base_url = settings.base_url.rstrip("/")
    details = _render_ticket_details(ticket)
    
    # Notify reporter
    if prefs.get("notify_priority_changed_requester", True) and ticket.reporter and ticket.reporter.email:
        from ..models import UserRole
        path = "/portal/ticket" if ticket.reporter.role == UserRole.requester else "/tickets"
        body = (
            f"Hello {ticket.reporter.display_name},\n\n"
            f"The priority level of your ticket {ticket.ticket_number} has been updated from "
            f"\"{old_priority.upper()}\" to \"{ticket.priority.value.upper()}\".\n\n"
            f"{details}\n"
            f"To view details or add notes, click below:\n"
            f"{base_url}{path}/{ticket.id}\n\n"
            "Best regards,\n"
            "Tema IT Team\n"
        )
        _fire(tenant, ticket.reporter.email,
              f"[OctoAssist] {ticket.ticket_number} — Priority Updated to {ticket.priority.value.title()}",
              body)

    # Notify assignee
    if prefs.get("notify_priority_changed_assignee", True) and ticket.assignee and ticket.assignee.email and ticket.assignee_id != ticket.reporter_id:
        from ..models import UserRole
        path = "/portal/ticket" if ticket.assignee.role == UserRole.requester else "/tickets"
        body = (
            f"Hello {ticket.assignee.display_name},\n\n"
            f"The priority level of ticket {ticket.ticket_number} assigned to you has been updated from "
            f"\"{old_priority.upper()}\" to \"{ticket.priority.value.upper()}\".\n\n"
            f"{details}\n"
            f"To view details, click below:\n"
            f"{base_url}{path}/{ticket.id}\n\n"
            "Best regards,\n"
            "OctoAssist System Service\n"
        )
        _fire(tenant, ticket.assignee.email,
              f"[OctoAssist] {ticket.ticket_number} — Priority Updated to {ticket.priority.value.title()}",
              body)


# ---------- Patch windows ----------

def patch_window_started(db: Session, window: PatchWindow) -> None:
    tenant = window.tenant if hasattr(window, "tenant") else db.get(Tenant, window.tenant_id)
    if tenant is None or not _mail_configured(tenant):
        return
    prefs = tenant.notification_settings or {}
    if not prefs.get("notify_patch_window_started", True):
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
    prefs = tenant.notification_settings or {}
    if not prefs.get("notify_patch_window_completed", True):
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
    prefs = tenant.notification_settings or {}
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
    if prefs.get("notify_change_submitted_requester", True):
        if change.requester and change.requester.email:
            _fire(tenant, change.requester.email,
                  f"[OctoAssist] {change.change_number} — Change Request Submitted",
                  body + f"\nView details: {base_url}/changes/{change.id}\n")
            sent.add(change.requester.email)
        # Also notify tenant.notification_email if configured
        if tenant.notification_email and tenant.notification_email not in sent:
            _fire(tenant, tenant.notification_email,
                  f"[OctoAssist] {change.change_number} — under CAB review",
                  body + f"\nView details: {base_url}/changes/{change.id}\n")
            sent.add(tenant.notification_email)
            
    if prefs.get("notify_change_submitted_cab", True):
        # Phase J: every CAB member gets the change for review
        for cab_email in _cab_emails(db, change.tenant_id, exclude=sent):
            _fire(tenant, cab_email,
                  f"[OctoAssist · CAB] {change.change_number} — needs your review",
                  body + f"\n— You receive this as a CAB member.\nApprove or reject at: {base_url}/changes/{change.id}\n")
