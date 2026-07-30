"""Outbound email (password-reset links + notifications).

When ``settings.smtp_enabled`` is False the mailer LOGS the message (including any
link) at INFO instead of sending — so the forgot/reset flow is fully testable
without a mail server. When enabled it sends via SMTP (STARTTLS) using stdlib
``smtplib`` on a worker thread, so the async event loop is never blocked.

Sending failures NEVER raise to the caller: they are logged and swallowed, so an
SMTP hiccup cannot break (or leak information from) the forgot-password flow.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("mailer")


def _send_sync(to: str, subject: str, html: str, text: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg.set_content(text or " ")
    if html:
        msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


async def send_email(to: str, subject: str, html: str, text: str | None = None) -> None:
    """Send an email, or log it when SMTP is disabled. Best-effort: a send failure
    is logged, not raised."""
    body = text or ""
    if not settings.smtp_enabled:
        log.info("email_logged", to=to, subject=subject, body=body or html)
        return
    try:
        await run_in_threadpool(_send_sync, to, subject, html, body)
        log.info("email_sent", to=to, subject=subject)
    except Exception as exc:  # noqa: BLE001 — email failures must not break the flow
        log.error("email_send_failed", to=to, subject=subject, error=str(exc))
