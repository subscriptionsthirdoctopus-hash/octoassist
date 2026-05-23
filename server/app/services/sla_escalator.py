import logging
import threading
import time
from datetime import datetime, timezone, timedelta

from ..database import SessionLocal
from ..models import Ticket, TicketPriority, User, UserRole, Tenant, TicketStatus
from ..services.audit import record
from ..models import TicketEventKind
from ..services.email import send_email, EmailError

log = logging.getLogger("octoassist.sla_escalator")

TICK_SECONDS = 300  # wake up every 5 minutes
_STARTED = False
_LOCK = threading.Lock()

def _tick_once() -> int:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        # Open tickets (status NOT resolved, closed, cancelled)
        open_tickets = (
            db.query(Ticket)
            .filter(Ticket.status.notin_([TicketStatus.resolved, TicketStatus.closed, TicketStatus.cancelled]))
            .all()
        )
        escalated_count = 0
        for t in open_tickets:
            escalate_response = False
            escalate_resolution = False
            
            # Near breach response (< 30 minutes remaining)
            if not t.first_response_at and t.due_response_at and not t.sla_response_escalated:
                if now > t.due_response_at - timedelta(minutes=30):
                    escalate_response = True
                    
            # Near breach resolution (< 30 minutes remaining)
            if t.due_resolution_at and not t.sla_resolution_escalated:
                if now > t.due_resolution_at - timedelta(minutes=30):
                    escalate_resolution = True
                    
            if escalate_response or escalate_resolution:
                log.info("Ticket %s nearing SLA breach. Escalating to Critical priority.", t.ticket_number)
                
                # 1. Update priority to Critical
                old_prio = t.priority
                t.priority = TicketPriority.critical
                
                # 2. Auto-assign to first active admin/agent if unassigned
                assigned_msg = ""
                if not t.assignee_id:
                    lead_tech = (
                        db.query(User)
                        .filter(User.tenant_id == t.tenant_id, User.role.in_([UserRole.admin, UserRole.agent]), User.is_active == True)
                        .first()
                    )
                    if lead_tech:
                        t.assignee_id = lead_tech.id
                        assigned_msg = f"Auto-assigned to lead staff {lead_tech.email}."
                        log.info("Ticket %s was unassigned. Auto-assigned to %s", t.ticket_number, lead_tech.email)
                
                # 3. Log formal TicketEvent
                escalation_reason = []
                if escalate_response:
                    t.sla_response_escalated = True
                    escalation_reason.append("Response SLA")
                if escalate_resolution:
                    t.sla_resolution_escalated = True
                    escalation_reason.append("Resolution SLA")
                    
                reason_str = " and ".join(escalation_reason)
                note_str = f"SLA Escalation Engine: Ticket has breached or is nearing breach of {reason_str}. Priority elevated to Critical. {assigned_msg}"
                
                record(
                    db,
                    ticket=t,
                    actor=None,  # system action
                    kind=TicketEventKind.priority_changed,
                    before={"priority": old_prio.value},
                    after={"priority": "critical"},
                    note=note_str
                )
                
                # 4. Fire email alert via Helpdesk@temaindia.com using the decrypted SMTP credentials
                tenant = db.get(Tenant, t.tenant_id)
                if tenant and tenant.smtp_host and tenant.smtp_from:
                    # Let's find admins to notify
                    admins = (
                        db.query(User)
                        .filter(User.tenant_id == t.tenant_id, User.role == UserRole.admin, User.is_active == True)
                        .all()
                    )
                    to_emails = [u.email for u in admins if u.email]
                    if to_emails:
                        subject = f"[SLA ESCALATION] Ticket {t.ticket_number} is nearing breach!"
                        body = (
                            f"ALERT: Ticket {t.ticket_number} is nearing {reason_str} breach!\n\n"
                            f"Subject: {t.title}\n"
                            f"Status: {t.status.value}\n"
                            f"Priority has been escalated to CRITICAL.\n"
                            f"SLA Due Date: {t.due_resolution_at or t.due_response_at}\n\n"
                            f"Link to Ticket: http://localhost:8000/tickets/{t.id}\n"
                        )
                        for email in to_emails:
                            try:
                                send_email(tenant=tenant, to=email, subject=subject, body_text=body)
                                log.info("Escalation email sent to %s for ticket %s", email, t.ticket_number)
                            except Exception as e:
                                log.error("Failed to send escalation email to %s: %s", email, e)
                
                escalated_count += 1
                db.commit()
        return escalated_count
    finally:
        db.close()

def _run() -> None:
    log.info("SLA escalator loop start (tick=%ds)", TICK_SECONDS)
    while True:
        try:
            n = _tick_once()
            if n:
                log.info("SLA escalator escalated %d ticket(s) this tick", n)
        except Exception as e:
            log.exception("SLA escalator tick errored: %s", e)
        time.sleep(TICK_SECONDS)

def start_in_background() -> None:
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        t = threading.Thread(target=_run, name="sla-escalator", daemon=True)
        t.start()
        _STARTED = True
        log.info("SLA escalator thread started (daemon)")
