from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import settings

request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
security_logger = logging.getLogger("glycofy.security")
alert_logger = logging.getLogger("glycofy.security.alert")
access_logger = logging.getLogger("glycofy.access")

_STANDARD_FIELDS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or request_id_context.get()
        if request_id:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in _STANDARD_FIELDS and key not in payload and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
    # Our middleware emits correlated JSON access events; suppress Uvicorn's
    # duplicate unstructured access line.
    logging.getLogger("uvicorn.access").disabled = True


def new_request_id(candidate: str | None) -> str:
    if candidate and 8 <= len(candidate) <= 128 and all(ch.isalnum() or ch in "-_." for ch in candidate):
        return candidate
    return uuid4().hex


def request_id() -> str:
    return request_id_context.get()


def privacy_safe_client_id(request: Request) -> str:
    address = request.client.host if request.client else "unknown"
    digest = hmac.new(settings.JWT_SECRET.encode(), address.encode(), hashlib.sha256).hexdigest()
    return digest[:20]


def emit_security_log(
    event_type: str,
    outcome: str,
    *,
    severity: str = "info",
    user_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    fields = {
        "event_type": event_type,
        "outcome": outcome,
        "user_id": user_id,
        "details": details or {},
    }
    level = logging.ERROR if severity == "alert" else logging.WARNING if severity == "warning" else logging.INFO
    target = alert_logger if severity == "alert" else security_logger
    target.log(level, "security_event", extra=fields)
    if severity == "alert":
        from app.alert_email import queue_security_alert

        queue_security_alert(
            {
                "event_type": event_type,
                "outcome": outcome,
                "user_id": user_id,
                "request_id": request_id() or None,
            }
        )


def record_security_event(
    db: Session,
    request: Request,
    event_type: str,
    outcome: str,
    *,
    severity: str = "info",
    user_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    # Local import avoids an import cycle while models register this table.
    from app.models import SecurityAuditEvent

    event = SecurityAuditEvent()
    event.occurred_at = datetime.utcnow()
    event.event_type = event_type
    event.outcome = outcome
    event.severity = severity
    event.user_id = user_id
    event.request_id = request_id() or None
    event.client_id_hash = privacy_safe_client_id(request)
    event.event_metadata = details or {}
    db.add(event)
    db.commit()
    emit_security_log(event_type, outcome, severity=severity, user_id=user_id, details=details)
