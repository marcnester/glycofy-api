from __future__ import annotations

import logging
import queue
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from typing import Any

from app.config import settings

logger = logging.getLogger("glycofy.feedback_delivery")
_QUEUE: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=100)
_LOCK = threading.Lock()
_WORKER_STARTED = False
_LAST_SENT = 0.0


def email_delivery_configured() -> bool:
    return bool(
        settings.BETA_FEEDBACK_EMAIL_ENABLED
        and settings.ADMIN_EMAILS
        and settings.SMTP_HOST
        and settings.SMTP_FROM_EMAIL
    )


def build_feedback_message(event: dict[str, Any]) -> EmailMessage:
    recipients = settings.csv_values(settings.ADMIN_EMAILS)
    operations_url = f"{(settings.PUBLIC_BASE_URL or '').rstrip('/')}/ui/operations.html"
    message = EmailMessage()
    message["To"] = ", ".join(recipients)
    message["From"] = settings.SMTP_FROM_EMAIL or "noreply@glycofy.ai"
    message["Subject"] = f"[Glycofy beta] New {event['category']} feedback"
    message.set_content(
        "New beta feedback is ready for review.\n\n"
        f"Category: {event['category']}\n"
        f"Rating: {event.get('rating') or 'not provided'}\n"
        f"Page: {event['page_path']}\n"
        f"Request ID: {event.get('request_id') or 'unavailable'}\n\n"
        f"Review it in Glycofy Operations: {operations_url}\n\n"
        "The written feedback, user identity, meals, health information, and training data "
        "are intentionally omitted from this notification.\n"
    )
    return message


def _deliver(event: dict[str, Any]) -> None:
    message = build_feedback_message(event)
    if settings.SMTP_USE_TLS:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(message)
    else:
        with smtplib.SMTP_SSL(
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            timeout=15,
            context=ssl.create_default_context(),
        ) as smtp:
            if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(message)


def _worker() -> None:
    while True:
        event = _QUEUE.get()
        try:
            _deliver(event)
            logger.info("feedback_email_sent", extra={"request_id": event.get("request_id")})
        except Exception:
            logger.exception("feedback_email_failed", extra={"request_id": event.get("request_id")})
        finally:
            _QUEUE.task_done()


def _ensure_worker() -> None:
    global _WORKER_STARTED
    with _LOCK:
        if _WORKER_STARTED:
            return
        threading.Thread(target=_worker, name="glycofy-feedback-email", daemon=True).start()
        _WORKER_STARTED = True


def queue_feedback_notification(event: dict[str, Any]) -> bool:
    global _LAST_SENT
    if not email_delivery_configured():
        return False

    now = time.monotonic()
    with _LOCK:
        if now - _LAST_SENT < settings.BETA_FEEDBACK_EMAIL_COOLDOWN_SECONDS:
            return False
        _LAST_SENT = now

    _ensure_worker()
    try:
        _QUEUE.put_nowait(event)
        return True
    except queue.Full:
        logger.error("feedback_email_queue_full")
        return False
