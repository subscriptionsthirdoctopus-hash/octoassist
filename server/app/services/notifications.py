"""Outbound notifications — fire-and-forget around domain events.

Every public function here:
  - Catches all exceptions; never blocks the caller.
  - Decides whether to send (e.g. only if mail is configured, only to a
    real recipient).
  - Composes a short subject + plain-text body. Plain text is the default:
    it keeps the templates trivial and renders cleanly in any Microsoft
    mailbox. The one exception is send_software_expiry_digest, which the
    customer asked for as a table — it sends an HTML table *alongside* the
    plaintext body, so non-HTML clients still get a fixed-width one.

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
    Agent, Change, PatchWindow, Tenant, Ticket, TicketComment, TicketEventKind, User, Problem,
)

log = logging.getLogger("octoassist.notify")


def _mail_configured(tenant: Tenant) -> bool:
    p = (tenant.mail_provider or "smtp").lower()
    if p == "graph":
        return bool(tenant.graph_tenant_id and tenant.graph_client_id and tenant.graph_client_secret and tenant.graph_from)
    return bool(tenant.smtp_host and tenant.smtp_port and tenant.smtp_from)


def _fire(tenant: Tenant, to: str, subject: str, body: str,
          body_html: str | None = None) -> None:
    """Send in a background thread — never blocks the request thread.

    body_html is optional and defaults to None, so every existing plaintext
    caller is unaffected. Pass it when the message is genuinely tabular; the
    plaintext body is still required and is what non-HTML clients receive.
    """
    if not _mail_configured(tenant):
        return
    if not to:
        return

    def _go():
        try:
            send_email(tenant=tenant, to=to, subject=subject, body_text=body,
                       body_html=body_html)
            log.info("notify sent: %r → %s", subject, to)
        except EmailError as e:
            log.warning("notify failed: %r → %s: %s", subject, to, e)
        except Exception as e:  # noqa: BLE001
            log.exception("notify crashed: %r → %s: %s", subject, to, e)

    threading.Thread(target=_go, daemon=True).start()


def _cab_emails(db: Session, tenant_id: int, exclude: set[str] | None = None) -> list[str]:
    """Return active CAB-member emails for the tenant. The exclude set prevents
    sending duplicate copies to people already on the primary recipient list."""
    from ..models import CabCommittee, CabCommitteeMember
    committee = db.query(CabCommittee).filter(
        CabCommittee.tenant_id == tenant_id,
        CabCommittee.name == "Change Management"
    ).first()
    
    if not committee:
        return []

    rows = (db.query(User.email)
              .join(CabCommitteeMember, User.id == CabCommitteeMember.user_id)
              .filter(User.tenant_id == tenant_id,
                      CabCommitteeMember.committee_id == committee.id,
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
    recipients = set()
    if tenant.notification_email:
        recipients.add(tenant.notification_email)
    if window.created_by and window.created_by.email:
        recipients.add(window.created_by.email)
    for to in recipients:
        _fire(tenant, to,
              f"[OctoAssist] Patch window started — {window.name}",
              body)


def patch_window_completed(db: Session, window: PatchWindow) -> None:
    tenant = window.tenant if hasattr(window, "tenant") else db.get(Tenant, window.tenant_id)
    if tenant is None or not _mail_configured(tenant):
        return
    prefs = tenant.notification_settings or {}
    if not prefs.get("notify_patch_window_completed", True):
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
    recipients = set()
    if tenant.notification_email:
        recipients.add(tenant.notification_email)
    if window.created_by and window.created_by.email:
        recipients.add(window.created_by.email)
    for to in recipients:
        _fire(tenant, to,
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
    
    disclaimer = (
        "\n________________________________\n"
        "This email (including any attachment(s) hereto) contains confidential or privileged information intended solely for the designated individual or entity to whom it is addressed. If you are not the intended recipient, please delete this message permanently and notify the sender immediately. Any unauthorized use, disclosure or distribution is strictly prohibited. TEMA India Pvt Ltd is not liable for the improper transmission of this message or any damage sustained as a result of it.\n"
        "________________________________\n"
    )
    
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
                  body + f"\nView details: {base_url}/changes/{change.id}\n" + disclaimer)
            sent.add(change.requester.email)
        # Also notify tenant.notification_email if configured
        if tenant.notification_email and tenant.notification_email not in sent:
            _fire(tenant, tenant.notification_email,
                  f"[OctoAssist] {change.change_number} — under CAB review",
                  body + f"\nView details: {base_url}/changes/{change.id}\n" + disclaimer)
            sent.add(tenant.notification_email)
            
    if prefs.get("notify_change_submitted_cab", True):
        # Phase J: every CAB member gets the change for review
        for cab_email in _cab_emails(db, change.tenant_id, exclude=sent):
            _fire(tenant, cab_email,
                  f"[OctoAssist · CAB] {change.change_number} — needs your review",
                  body + f"\nView details: {base_url}/changes/{change.id}\n\n— You receive this as a CAB member.\n" + disclaimer)



# ---------- Software Actions Completed ----------

def remote_action_completed(db: Session, action) -> None:
    from ..models import Tenant, RemoteActionStatus
    tenant = db.get(Tenant, action.tenant_id)
    if tenant is None or not _mail_configured(tenant):
        return

    # Check if this action is software related
    is_software = False
    action_name = ""

    label = action.params.get("label", "") if action.params else ""
    kind = action.kind.value

    if kind == "run_executable":
        is_software = True
        action_name = label or f"Installation/Update of software (URL: {action.params.get('url')})"
    elif kind == "custom_powershell" and ("Uninstall" in label or "uninstall" in label or "install" in label or "Install" in label):
        is_software = True
        action_name = label or "Uninstall/Software script"

    if not is_software:
        return

    status_str = action.status.value.upper()
    agent = action.agent

    subject = f"[OctoAssist] Software Action {status_str} — {action_name} on {agent.hostname}"

    from ..config import settings
    base_url = settings.base_url.rstrip("/")

    body = (
        f"A remote software action has completed with status: {status_str}\n\n"
        f"Action: {action_name}\n"
        f"Agent/Machine: {agent.hostname} (ID: {agent.id})\n"
        f"Status: {status_str}\n"
        f"Exit Code: {action.exit_code if action.exit_code is not None else '—'}\n"
        f"Finished At: {action.finished_at}\n\n"
    )
    if action.stderr:
        body += f"Error Output (stderr):\n{action.stderr}\n\n"
    if action.stdout:
        body += f"Standard Output (stdout):\n{action.stdout[:1000]}\n\n"

    sent = set()
    if action.created_by and action.created_by.email:
        _fire(tenant, action.created_by.email, subject, body)
        sent.add(action.created_by.email)

    if tenant.notification_email and tenant.notification_email not in sent:
        _fire(tenant, tenant.notification_email, subject, body)


def patch_window_created(db: Session, window: PatchWindow) -> None:
    tenant = window.tenant if hasattr(window, "tenant") else db.get(Tenant, window.tenant_id)
    if tenant is None or not _mail_configured(tenant):
        return
    prefs = tenant.notification_settings or {}
    if not prefs.get("notify_patch_window_created", True):
        return
    from ..config import settings
    base_url = settings.base_url.rstrip("/")
    n_targets = len(window.targets)
    n_pkgs = len(window.selected_packages or [])
    sch_for = "—"
    if window.scheduled_for:
        sch_for = window.scheduled_for.strftime("%Y-%m-%d %H:%M UTC")
    body = (
        f"A new patch window has been scheduled: {window.name}\n"
        f"Scheduled for: {sch_for}\n"
        f"Targets: {n_targets} endpoint{'s' if n_targets != 1 else ''}\n"
        f"Approved packages: {n_pkgs}\n"
        f"Auto-execute: {'on' if window.auto_execute else 'off (manual tracking)'}\n"
        f"\n{window.description or ''}\n"
        f"\nView details: {base_url}/patches/windows/{window.id}\n"
    )
    recipients = set()
    if tenant.notification_email:
        recipients.add(tenant.notification_email)
    if window.created_by and window.created_by.email:
        recipients.add(window.created_by.email)
    for to in recipients:
        _fire(tenant, to,
              f"[OctoAssist] Patch window scheduled — {window.name}",
              body)


def change_status_changed(db: Session, change: Change, old_status: str, actor: User, note: str = "") -> None:
    tenant = change.tenant if hasattr(change, "tenant") else db.get(Tenant, change.tenant_id)
    if tenant is None or not _mail_configured(tenant):
        return
    prefs = tenant.notification_settings or {}
    if not prefs.get("notify_change_status_changed", True):
        return
    from ..config import settings
    base_url = settings.base_url.rstrip("/")
    body = (
        f"Change Request Status Transition: {change.change_number}\n"
        f"Title: {change.title}\n"
        f"Transition: {old_status} → {change.status.value}\n"
        f"Updated by: {actor.display_name if actor else 'System'}\n"
    )
    if note and note.strip():
        body += f"Note: {note.strip()}\n"
    body += f"\nView Change details: {base_url}/changes/{change.id}\n"

    recipients = set()
    if tenant.notification_email:
        recipients.add(tenant.notification_email)
    if change.requester and change.requester.email:
        recipients.add(change.requester.email)
    if change.implementer and change.implementer.email:
        recipients.add(change.implementer.email)
    for to in recipients:
        _fire(tenant, to,
              f"[OctoAssist] Change {change.change_number} — Status is {change.status.value}",
              body)


def problem_created(db: Session, problem: Problem) -> None:
    tenant = problem.tenant if hasattr(problem, "tenant") else db.get(Tenant, problem.tenant_id)
    if tenant is None or not _mail_configured(tenant):
        return
    prefs = tenant.notification_settings or {}
    if not prefs.get("notify_problem_created", True):
        return
    from ..config import settings
    base_url = settings.base_url.rstrip("/")
    body = (
        f"A new Problem record has been opened: {problem.problem_number}\n"
        f"Title: {problem.title}\n"
        f"Priority: {problem.priority.value}\n"
        f"Reporter: {problem.reporter.display_name if problem.reporter else '—'}\n"
        f"Assignee: {problem.assignee.display_name if problem.assignee else 'Unassigned'}\n"
        f"\nDescription:\n{problem.description or ''}\n"
        f"\nView Problem details: {base_url}/problems/{problem.id}\n"
    )
    recipients = set()
    if tenant.notification_email:
        recipients.add(tenant.notification_email)
    if problem.reporter and problem.reporter.email:
        recipients.add(problem.reporter.email)
    if problem.assignee and problem.assignee.email:
        recipients.add(problem.assignee.email)
    for to in recipients:
        _fire(tenant, to,
              f"[OctoAssist] New Problem Opened — {problem.problem_number}",
              body)


def problem_status_changed(db: Session, problem: Problem, old_status: str) -> None:
    tenant = problem.tenant if hasattr(problem, "tenant") else db.get(Tenant, problem.tenant_id)
    if tenant is None or not _mail_configured(tenant):
        return
    prefs = tenant.notification_settings or {}
    if not prefs.get("notify_problem_status_changed", True):
        return
    from ..config import settings
    base_url = settings.base_url.rstrip("/")
    body = (
        f"Problem Status Update: {problem.problem_number}\n"
        f"Title: {problem.title}\n"
        f"Transition: {old_status} → {problem.status.value}\n"
        f"Reporter: {problem.reporter.display_name if problem.reporter else '—'}\n"
        f"Assignee: {problem.assignee.display_name if problem.assignee else 'Unassigned'}\n"
    )
    if problem.root_cause:
        body += f"\nRoot Cause:\n{problem.root_cause}\n"
    if problem.workaround:
        body += f"\nWorkaround:\n{problem.workaround}\n"
    body += f"\nView Problem details: {base_url}/problems/{problem.id}\n"
    recipients = set()
    if tenant.notification_email:
        recipients.add(tenant.notification_email)
    if problem.reporter and problem.reporter.email:
        recipients.add(problem.reporter.email)
    if problem.assignee and problem.assignee.email:
        recipients.add(problem.assignee.email)
    for to in recipients:
        _fire(tenant, to,
              f"[OctoAssist] Problem {problem.problem_number} — Status is {problem.status.value}",
              body)


def send_daily_admin_audit_digest(db: Session) -> None:
    """Consolidate the last 24 hours of admin logs daily and email a structured plaintext digest strictly to Arun at arun.d@temaindia.com."""
    from datetime import datetime, timezone, timedelta
    from ..models import AuditLog, Tenant

    recipient = "arun.d@temaindia.com"

    # Get any tenant to use for mail configuration settings
    tenant = db.query(Tenant).first()
    if tenant is None:
        log.warning("No tenant found; skipping daily admin audit digest.")
        return

    if not _mail_configured(tenant):
        log.warning("Mail is not configured for the primary tenant; daily admin audit digest skipped.")
        return

    # Calculate last 24 hours (UTC)
    now_utc = datetime.now(timezone.utc)
    twenty_four_hours_ago = now_utc - timedelta(hours=24)

    # Query logs in last 24 hours
    logs = (
        db.query(AuditLog)
        .filter(AuditLog.created_at >= twenty_four_hours_ago)
        .order_by(AuditLog.created_at.desc())
        .all()
    )

    # Format report in plain text
    IST = timezone(timedelta(hours=5, minutes=30), name="IST")
    subject = f"[OctoAssist] Daily Admin Audit Digest — {now_utc.astimezone(IST).strftime('%d-%b-%Y')}"

    if not logs:
        body = (
            "============================================================\n"
            "                OCTOASSIST ADMIN AUDIT DIGEST               \n"
            "============================================================\n\n"
            "No administrator actions were recorded in the last 24 hours.\n\n"
            "============================================================\n"
        )
    else:
        body = (
            "============================================================\n"
            "                OCTOASSIST ADMIN AUDIT DIGEST               \n"
            "============================================================\n\n"
            f"Digest Period (Last 24 Hours): {twenty_four_hours_ago.astimezone(IST).strftime('%d-%b-%Y %I:%M %p IST')} to {now_utc.astimezone(IST).strftime('%d-%b-%Y %I:%M %p IST')}\n"
            f"Total Actions Captured: {len(logs)}\n\n"
            "------------------------- DETAILS -------------------------\n\n"
        )
        for i, l in enumerate(logs, 1):
            log_time = l.created_at
            if log_time.tzinfo is None:
                log_time = log_time.replace(tzinfo=timezone.utc)
            time_ist = log_time.astimezone(IST).strftime("%d-%b-%Y %I:%M:%S %p IST")
            actor = l.user.email if l.user else "System/Unknown"

            body += (
                f"{i}. [{time_ist}]\n"
                f"   Admin/Actor: {actor}\n"
                f"   IP Address:  {l.ip_address or '—'}\n"
                f"   Action:      {l.action}\n"
                f"   Details:     {l.details}\n"
                "------------------------------------------------------------\n"
            )
        body += "============================================================\n"

    # Send the email strictly to arun.d@temaindia.com using the helper
    _fire(tenant, recipient, subject, body)


def agent_uninstallation_triggered(db: Session, agent: Agent) -> None:
    """Notify Arun strictly that an agent uninstallation has been triggered on asset deletion."""
    tenant = agent.tenant
    if tenant is None or not _mail_configured(tenant):
        return
    
    recipient = "arun.d@temaindia.com"
    subject = f"[OctoAssist] Endpoint Uninstallation Triggered — {agent.hostname}"
    
    body = (
        "============================================================\n"
        "               OCTOASSIST ENDPOINT UNINSTALLATION           \n"
        "============================================================\n\n"
        f"Endpoint Hostname:  {agent.hostname}\n"
        f"Machine ID:         {agent.machine_id}\n"
        f"Uninstallation:     Started Immediately (Remote Command Queued)\n"
        f"Status:             Deleted from Dashboard (Uninstall Pending)\n\n"
        "The background scheduled task 'OctoAssistAgent' on the endpoint\n"
        "will unregister itself and delete all local agent directory files\n"
        "on its next check-in (~30 seconds).\n\n"
        "============================================================\n"
    )
    _fire(tenant, recipient, subject, body)





def send_software_expiry_digest(db: Session, horizon_days: int | None = None) -> int:
    """One consolidated email listing every software subscription that has
    lapsed or lapses within the horizon — TEMA action item 5.

    Deliberately one message per tenant per day, not one per subscription. A
    per-item notification stream is what the customer asked us to replace: ten
    licences renewing in the same week produced ten separate mails, none of
    which showed the whole picture.

    Sent as an HTML table with a fixed-width plaintext alternative, so it reads
    as a table whether or not the client renders HTML.

    Returns the number of tenants mailed, so the scheduler can log it.
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    from . import subscriptions as subs_svc

    horizon = subs_svc.DEFAULT_HORIZON_DAYS if horizon_days is None else horizon_days
    IST = _tz(_td(hours=5, minutes=30), name="IST")
    today = subs_svc._today()
    sent = 0

    for tenant in db.query(Tenant).all():
        if not _mail_configured(tenant):
            continue
        recipient = tenant.notification_email
        if not recipient:
            continue
        rows = subs_svc.expiring_soon(db, tenant.id, horizon_days=horizon)
        if not rows:
            continue  # nothing to chase — silence is correct, not a missed send

        expired = [r for r in rows if (r.expires_on - today).days < 0]
        upcoming = [r for r in rows if (r.expires_on - today).days >= 0]

        def _when(sub) -> str:
            days = (sub.expires_on - today).days
            if days < 0:
                return f"expired {abs(days)}d ago"
            if days == 0:
                return "expires today"
            return f"in {days}d"

        subject = (f"[OctoAssist] Software expiry — {len(rows)} item(s) need attention "
                   f"({_dt.now(IST).strftime('%d-%b-%Y')})")

        # ---- plaintext: fixed-width columns so it still lines up ----
        head = f"{'Software':<34} {'Vendor':<18} {'Seats':>5} {'Expires':<12} {'Status':<18} PO"
        lines = [
            "OCTOASSIST — SOFTWARE SUBSCRIPTION EXPIRY",
            "=" * len(head),
            f"Tenant: {tenant.name}   Horizon: next {horizon} days   "
            f"Generated: {_dt.now(IST).strftime('%d-%b-%Y %H:%M IST')}",
            f"{len(expired)} already expired, {len(upcoming)} expiring within {horizon} days.",
            "",
            head,
            "-" * len(head),
        ]
        for s in rows:
            lines.append(
                f"{(s.software_name or '')[:34]:<34} "
                f"{(s.vendor or '—')[:18]:<18} "
                f"{(str(s.seats) if s.seats else '—'):>5} "
                f"{s.expires_on.strftime('%d/%m/%Y'):<12} "
                f"{_when(s):<18} "
                f"{s.po_reference or '—'}"
            )
        lines += ["-" * len(head), "",
                  "Manage these at /subscriptions in OctoAssist.", ""]
        body_text = "\n".join(lines)

        # ---- HTML table ----
        def _esc(v: str) -> str:
            return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

        cells = []
        for s in rows:
            days = (s.expires_on - today).days
            colour = "#b91c1c" if days < 0 else ("#b45309" if days <= 7 else "#1f2937")
            cells.append(
                "<tr>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;'>{_esc(s.software_name)}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;'>{_esc(s.vendor or '—')}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:right;'>{_esc(s.seats or '—')}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;white-space:nowrap;'>{s.expires_on.strftime('%d/%m/%Y')}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;color:{colour};font-weight:600;white-space:nowrap;'>{_esc(_when(s))}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;'>{_esc(s.po_reference or '—')}</td>"
                "</tr>"
            )
        header_cells = "".join(
            f"<th style='padding:6px 10px;text-align:left;border-bottom:2px solid #0c64c0;"
            f"font-size:12px;text-transform:uppercase;letter-spacing:.4px;'>{h}</th>"
            for h in ("Software", "Vendor", "Seats", "Expires", "Status", "PO")
        )
        body_html = (
            "<div style=\"font-family:Segoe UI,Arial,sans-serif;color:#1f2937;\">"
            "<h2 style='margin:0 0 4px;font-size:18px;'>Software subscription expiry</h2>"
            f"<p style='margin:0 0 14px;color:#6b7280;font-size:13px;'>"
            f"{_esc(tenant.name)} &middot; horizon next {horizon} days &middot; "
            f"{_dt.now(IST).strftime('%d-%b-%Y %H:%M IST')}<br>"
            f"<strong>{len(expired)}</strong> already expired, "
            f"<strong>{len(upcoming)}</strong> expiring within {horizon} days.</p>"
            "<table style='border-collapse:collapse;font-size:13px;min-width:640px;'>"
            f"<thead><tr>{header_cells}</tr></thead><tbody>{''.join(cells)}</tbody></table>"
            "<p style='margin:14px 0 0;color:#6b7280;font-size:12px;'>"
            "Manage these at /subscriptions in OctoAssist.</p></div>"
        )

        _fire(tenant, recipient, subject, body_text, body_html=body_html)
        sent += 1

    return sent
