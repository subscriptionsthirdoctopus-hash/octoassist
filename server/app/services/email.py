"""Outbound email — SMTP or Microsoft Graph (per tenant).

Phase 7 added SMTP. Phase 8 adds Graph API as an alternative for M365
tenants that block SMTP AUTH (the modern default), and encrypts secrets
at rest via app/crypto.py.

Same send_email(...) entry point either way — the function picks the
backend from tenant.mail_provider.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

import httpx

from ..crypto import decrypt

log = logging.getLogger("octoassist.email")


class EmailError(RuntimeError):
    pass


# ---------- Top-level dispatcher ----------

def send_email(
    *,
    tenant,
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> str:
    if not to:
        raise EmailError("Recipient empty")
    provider = (tenant.mail_provider or "smtp").lower()
    if provider == "graph":
        return _send_graph(tenant=tenant, to=to, subject=subject,
                           body_text=body_text, body_html=body_html)
    return _send_smtp(tenant=tenant, to=to, subject=subject,
                      body_text=body_text, body_html=body_html)


# ---------- SMTP (Phase 7) ----------

def _send_smtp(*, tenant, to, subject, body_text, body_html=None) -> str:
    if not tenant.smtp_host or not tenant.smtp_port:
        raise EmailError("SMTP not configured (host or port missing)")
    if not tenant.smtp_from:
        raise EmailError("SMTP From address not set")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = tenant.smtp_from
    msg["To"] = to
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    host = tenant.smtp_host
    port = int(tenant.smtp_port)
    user = tenant.smtp_username or None
    pwd = decrypt(tenant.smtp_password) or None
    use_tls = bool(tenant.smtp_use_tls)

    try:
        if port == 465:
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
        err = e.smtp_error.decode(errors="replace") if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
        raise EmailError(f"SMTP auth failed: {e.smtp_code} {err}") from e
    except smtplib.SMTPRecipientsRefused as e:
        raise EmailError(f"Recipient refused: {to} — {e.recipients}") from e
    except smtplib.SMTPConnectError as e:
        raise EmailError(f"SMTP connect failed: {e}") from e
    except smtplib.SMTPException as e:
        raise EmailError(f"SMTP error: {e}") from e
    except (ConnectionError, OSError) as e:
        raise EmailError(f"Network error reaching SMTP server: {e}") from e

    return f"OK (SMTP) — server response: {response or '{}'}"


# ---------- Microsoft Graph (Phase 8) ----------
#
# Setup at the customer's Azure tenant:
#   1. https://entra.microsoft.com → App registrations → New registration.
#      Single tenant, no redirect URI.
#   2. Note Directory (tenant) ID, Application (client) ID.
#   3. Certificates & secrets → New client secret. Copy the Value once.
#   4. API permissions → Microsoft Graph → Application permissions →
#      Mail.Send → Grant admin consent.
#   5. graph_from must be a real mailbox in this tenant the app has
#      Mail.Send rights on (typically a service-account mailbox).

GRAPH_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
GRAPH_SENDMAIL_URL = "https://graph.microsoft.com/v1.0/users/{from_addr}/sendMail"


def _send_graph(*, tenant, to, subject, body_text, body_html=None) -> str:
    if not (tenant.graph_tenant_id and tenant.graph_client_id and tenant.graph_client_secret):
        raise EmailError("Graph not configured (tenant id / client id / client secret missing)")
    if not tenant.graph_from:
        raise EmailError("Graph From address not set")

    secret = decrypt(tenant.graph_client_secret)
    if not secret:
        raise EmailError("Graph client secret could not be decrypted (key rotation?)")

    # 1. client_credentials token grant
    try:
        with httpx.Client(timeout=20) as cli:
            tok_resp = cli.post(
                GRAPH_TOKEN_URL.format(tenant=tenant.graph_tenant_id.strip()),
                data={
                    "client_id": tenant.graph_client_id.strip(),
                    "client_secret": secret,
                    "scope": "https://graph.microsoft.com/.default",
                    "grant_type": "client_credentials",
                },
            )
    except (httpx.HTTPError, OSError) as e:
        raise EmailError(f"Graph token endpoint unreachable: {e}") from e

    if tok_resp.status_code != 200:
        body = (tok_resp.text or "")[:300]
        raise EmailError(f"Graph token failed: {tok_resp.status_code} {body}")

    token = tok_resp.json().get("access_token")
    if not token:
        raise EmailError("Graph token endpoint returned no access_token")

    # 2. sendMail
    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "HTML" if body_html else "Text",
                "content": body_html or body_text,
            },
            "toRecipients": [{"emailAddress": {"address": to}}],
            "from": {"emailAddress": {"address": tenant.graph_from}},
        },
        "saveToSentItems": False,
    }
    try:
        with httpx.Client(timeout=30) as cli:
            r = cli.post(
                GRAPH_SENDMAIL_URL.format(from_addr=tenant.graph_from.strip()),
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                json=payload,
            )
    except (httpx.HTTPError, OSError) as e:
        raise EmailError(f"Graph sendMail unreachable: {e}") from e

    if r.status_code in (200, 202):
        return f"OK (Graph) — status {r.status_code}"
    raise EmailError(f"Graph sendMail failed: {r.status_code} {r.text[:300]}")


# ---------- Presets used by Settings UI ----------

PRESETS = {
    "office365": {
        "label": "Microsoft 365 SMTP (smtp.office365.com)",
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
    },
    "outlook": {
        "label": "Outlook.com SMTP (smtp-mail.outlook.com)",
        "smtp_host": "smtp-mail.outlook.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
    },
}
