"""Delete security audit records older than the configured retention period."""

from datetime import datetime, timedelta

from app.config import settings
from app.db import SessionLocal
from app.models import SecurityAuditEvent


def main() -> None:
    cutoff = datetime.utcnow() - timedelta(days=max(30, settings.SECURITY_AUDIT_RETENTION_DAYS))
    with SessionLocal.begin() as db:
        deleted = db.query(SecurityAuditEvent).filter(SecurityAuditEvent.occurred_at < cutoff).delete()
    print(f"Deleted {deleted} expired security audit event(s).")


if __name__ == "__main__":
    main()
