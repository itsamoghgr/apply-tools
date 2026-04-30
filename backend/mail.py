"""Tiny SMTP helper for sending mail through Gmail with an app password.

Gmail app passwords require 2-Step Verification on the Google account; the
user generates one at https://myaccount.google.com/apppasswords. We connect
over implicit TLS on port 465 (smtplib.SMTP_SSL) which is the simplest path
that doesn't require STARTTLS negotiation.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import formataddr


class GmailAuthError(RuntimeError):
    """Raised when Gmail rejects the credentials. Surface to the user as 401-ish."""


class GmailSendError(RuntimeError):
    """Raised for any other SMTP failure (network, recipient refused, etc)."""


def send_gmail(
    from_addr: str,
    app_password: str,
    to_addr: str,
    subject: str,
    body: str,
    from_name: str | None = None,
    html_body: str | None = None,
) -> None:
    """Send a plain-text email, with an optional HTML alternative.

    When `html_body` is provided we send a multipart/alternative message so
    HTML-capable clients render the formatted version (and load any embedded
    tracking pixel) while clients in plain-text mode still see `body`.
    """
    msg = EmailMessage()
    msg["From"] = formataddr((from_name or "", from_addr))
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    if html_body:
        # `add_alternative` automatically promotes the message to
        # multipart/alternative with the plain-text part listed first, which
        # is what RFC 2046 recommends so non-HTML clients fall through.
        msg.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(from_addr, app_password)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise GmailAuthError(
            "Gmail rejected the app password. Double-check the address and "
            "regenerate an app password at "
            "https://myaccount.google.com/apppasswords."
        ) from exc
    except smtplib.SMTPException as exc:
        raise GmailSendError(f"SMTP send failed: {exc}") from exc
