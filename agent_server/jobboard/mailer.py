"""Minimal SMTP sender for the Job Board alert.

Deliberately NOT a revival of backend/mail.py, which was deleted in 8cef324
along with its Gmail IMAP inbox and open/click-tracking sidecar. None of that is
wanted here: this sends one message to one address and nothing else.

Config lives in agent_server/.env (see config.py):
    JOBBOARD_SMTP_HOST / _PORT / _USER / _APP_PASSWORD
    JOBBOARD_ALERT_TO

For Gmail, JOBBOARD_SMTP_APP_PASSWORD must be an APP PASSWORD (Google account ->
Security -> 2-Step Verification -> App passwords), not the account password.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate

from agent_server.config import CONFIG
from agent_server.log import get_logger

logger = get_logger(__name__)

# Bounded so a hung SMTP server can't wedge the scheduler thread.
_TIMEOUT_S = 30.0


class MailNotConfigured(Exception):
    """SMTP credentials or the recipient are missing.

    Raised BEFORE any connection attempt so the caller can mark the alert run
    failed with a clear reason — and, crucially, leave the postings unstamped so
    they roll into the next successful send.
    """


class MailSendError(Exception):
    """The message could not be handed to the SMTP server."""


def is_configured() -> bool:
    """True when every field needed to send is present."""
    return bool(
        CONFIG.jobboard_smtp_host
        and CONFIG.jobboard_smtp_user
        and CONFIG.jobboard_smtp_app_password
        and CONFIG.jobboard_alert_to
    )


def send_mail(
    subject: str,
    html_body: str,
    text_body: str,
    *,
    to: str | None = None,
    from_name: str = "Job Board",
) -> None:
    """Send one multipart/alternative message. Raises on any failure.

    The plaintext part is not a courtesy — a text/html-only message scores badly
    with spam filters, and some clients render the raw markup.
    """
    if not is_configured():
        missing = [
            name
            for name, value in (
                ("JOBBOARD_SMTP_HOST", CONFIG.jobboard_smtp_host),
                ("JOBBOARD_SMTP_USER", CONFIG.jobboard_smtp_user),
                ("JOBBOARD_SMTP_APP_PASSWORD", CONFIG.jobboard_smtp_app_password),
                ("JOBBOARD_ALERT_TO", CONFIG.jobboard_alert_to),
            )
            if not value
        ]
        raise MailNotConfigured(f"missing SMTP config: {', '.join(missing)}")

    recipient = to or CONFIG.jobboard_alert_to
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, CONFIG.jobboard_smtp_user))
    msg["To"] = recipient
    msg["Date"] = formatdate(localtime=True)
    # set_content then add_alternative => text/plain first, text/html second,
    # which is the order mail clients expect for multipart/alternative.
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    host = CONFIG.jobboard_smtp_host
    port = CONFIG.jobboard_smtp_port
    log = logger.bind(host=host, port=port, to=recipient)

    try:
        context = ssl.create_default_context()
        if port == 587:
            # Explicit STARTTLS upgrade for submission ports.
            with smtplib.SMTP(host, port, timeout=_TIMEOUT_S) as server:
                server.starttls(context=context)
                server.login(CONFIG.jobboard_smtp_user, CONFIG.jobboard_smtp_app_password)
                server.send_message(msg)
        else:
            # Implicit TLS (465), the Gmail default used here.
            with smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT_S, context=context) as server:
                server.login(CONFIG.jobboard_smtp_user, CONFIG.jobboard_smtp_app_password)
                server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        # Overwhelmingly the first-run failure: a normal password was used
        # instead of an app password, so say so rather than echoing the raw code.
        raise MailSendError(
            f"SMTP auth rejected ({exc.smtp_code}). For Gmail, "
            "JOBBOARD_SMTP_APP_PASSWORD must be an app password, not your "
            "account password."
        ) from exc
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise MailSendError(f"SMTP send failed: {exc.__class__.__name__}: {exc}") from exc

    log.info("jobboard.alert_mailed", subject=subject)
