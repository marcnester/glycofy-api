from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger("glycofy.account_email")


def account_email_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_FROM_EMAIL)


def send_account_email(to: str, subject: str, body: str) -> bool:
    if not account_email_configured():
        return False
    message = EmailMessage()
    message["To"] = to
    message["From"] = settings.SMTP_FROM_EMAIL
    message["Subject"] = subject
    message.set_content(body)
    try:
        if settings.SMTP_USE_TLS:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
                    smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(message)
        else:
            with smtplib.SMTP_SSL(
                settings.SMTP_HOST, settings.SMTP_PORT, timeout=15, context=ssl.create_default_context()
            ) as smtp:
                if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
                    smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(message)
        return True
    except Exception:
        logger.exception("account_email_delivery_failed")
        return False
