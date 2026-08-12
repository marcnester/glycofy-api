"""Print a privacy-safe security event summary for an operational review."""

from datetime import datetime, timedelta

from sqlalchemy import func

from app.db import SessionLocal
from app.models import SecurityAuditEvent


def main() -> None:
    cutoff = datetime.utcnow() - timedelta(hours=24)
    with SessionLocal() as db:
        rows = (
            db.query(
                SecurityAuditEvent.event_type,
                SecurityAuditEvent.outcome,
                SecurityAuditEvent.severity,
                func.count(SecurityAuditEvent.id),
            )
            .filter(SecurityAuditEvent.occurred_at >= cutoff)
            .group_by(
                SecurityAuditEvent.event_type,
                SecurityAuditEvent.outcome,
                SecurityAuditEvent.severity,
            )
            .order_by(func.count(SecurityAuditEvent.id).desc())
            .all()
        )
    for event_type, outcome, severity, count in rows:
        print(f"{count:6d}  {severity:8s}  {event_type:36s}  {outcome}")


if __name__ == "__main__":
    main()
