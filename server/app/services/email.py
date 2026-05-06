"""Outbound email — tenant SMTP.

Phase 7 only wires send-test. Phase 8 will hook this into ticket events,
SLA breach alerts, and patch-window notifications.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger("octoassist.email")


class EmailError(RuntimeError):
    pass


def send_email(
    *,
    tenant,                 # Tenant model — uses tenant.smtp_*
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> str:
    """Send a plain + optional HTML email via the tenant's SMTP config.

    Returns the SMTP server's accept response. Raises EmailError on any
    failure (DNS, auth, refused recipient, TLS, …) with a human-readable
    message safe to surface in the UI.
    """
    if not tenant.smtp_host or not tenant.smtp_port:
        raise EmailError("SMTP not configured (host or port missing)")
    if not tenant.smtp_from:
        raise EmailError("SMTP From address not set")
    if not to:
        raise EmailError("Recipient empty")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = tenant.smtp_from
    msg["To"]      = to
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    host = tenant.smtp_host
    port = int(tenant.smtp_port)
    user = tenant.smtp_username or None
    pwd  = tenant.smtp_password or None
    use_tls = bool(tenant.smtp_use_tls)

    try:
        if port == 465:
            # Implicit TLS (SMTPS)
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=20, context=ctx) as s:
                if user and pwd:
                    s.login(user, pwd)
                response = s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.ehlo()
                if use_tls:
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
                if user and pwd:
                    s.login(user, pwd)
                response = s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise EmailError(f"SMTP auth failed: {e.smtp_code} {e.smtp_error.decode(errors='replace') if isinstance(e.smtp_error, bytes) else e.smtp_error}") from e
    except smtplib.SMTPRecipientsRefused as e:
        raise EmailError(f"Recipient refused: {to} — {e.recipients}") from e
    except smtplib.SMTPConnectError as e:
        raise EmailError(f"SMTP connect failed: {e}") from e
    except smtplib.SMTPException as e:
        raise EmailError(f"SMTP error: {e}") from e
    except (ConnectionError, OSError) as e:
        raise EmailError(f"Network error reaching SMTP server: {e}") from e

    return f"OK — server response: {response or '{}'}"


# ---------- preset configs ----------

PRESETS = {
    "office365": {
        "label": "Microsoft 365 (smtp.office365.com)",
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "note": ("Use a service-account mailbox with SMTP AUTH enabled. "
                 "Modern Microsoft tenants block SMTP AUTH by default — see "
                 "Microsoft 365 admin → Mail flow → SMTP AUTH per-mailbox."),
    },
    "outlook": {
        "label": "Outlook.com personal (smtp-mail.outlook.com)",
        "smtp_host": "smtp-mail.outlook.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "note": "Requires an outlook.com / hotmail.com app password.",
    },
}
