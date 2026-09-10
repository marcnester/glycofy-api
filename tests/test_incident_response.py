from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import SecurityAuditEvent
from app.routers import operations


def test_security_summary_is_actionable_and_privacy_safe() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime.utcnow()
    with Session(engine) as db:
        db.add_all(
            [
                SecurityAuditEvent(
                    occurred_at=now,
                    event_type="authentication_rate_limited",
                    outcome="denied",
                    severity="alert",
                    user_id=None,
                    request_id="request-alert-1",
                    client_id_hash="private-client-hash",
                    event_metadata={"private": "not returned"},
                ),
                SecurityAuditEvent(
                    occurred_at=now,
                    event_type="authentication_login",
                    outcome="failure",
                    severity="warning",
                    request_id="request-warning-1",
                    client_id_hash="another-private-hash",
                    event_metadata={},
                ),
                SecurityAuditEvent(
                    occurred_at=now - timedelta(days=2),
                    event_type="old_event",
                    outcome="ignored",
                    severity="alert",
                    request_id="old-request",
                    client_id_hash="old-client",
                    event_metadata={},
                ),
            ]
        )
        db.commit()

        payload = operations.security_summary(hours=24, db=db, _admin=SimpleNamespace(email="admin@example.com"))

    assert payload["events"] == 2
    assert payload["by_severity"] == {"alert": 1, "warning": 1}
    assert payload["by_event"]["authentication_rate_limited:denied"] == 1
    assert payload["recent_alerts"] == [
        {
            "event_type": "authentication_rate_limited",
            "outcome": "denied",
            "request_id": "request-alert-1",
            "occurred_at": now.isoformat(),
        }
    ]
    serialized = str(payload)
    assert "private-client-hash" not in serialized
    assert "private" not in serialized
    assert "user_id" not in serialized


def test_security_summary_uses_concealed_admin_authorization(monkeypatch) -> None:
    monkeypatch.setattr(operations.settings, "ADMIN_EMAILS", "owner@example.com")
    assert operations._require_admin(SimpleNamespace(email="OWNER@example.com"))

    try:
        operations._require_admin(SimpleNamespace(email="attacker@example.com"))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404
    else:
        raise AssertionError("Non-admin security summary access should be concealed")


def test_operations_dashboard_exposes_privacy_safe_security_pulse() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "ui" / "operations.html").read_text()
    script = (root / "ui" / "operations.js").read_text()

    assert "Security pulse" in page
    assert "/v1/operations/security-summary" in script
    assert "recent_alerts" in script
