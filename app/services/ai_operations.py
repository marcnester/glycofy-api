from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.db import SessionLocal
from app.models import AIOperationMetric

logger = logging.getLogger(__name__)


def record_ai_operation(
    operation: str,
    *,
    status: str,
    provider: str = "openai",
    model: str | None = None,
    prompt_version: str | None = None,
    latency_ms: int | None = None,
    usage: dict[str, Any] | None = None,
    cost_usd: float = 0.0,
    accepted_items: int | None = None,
    rejected_items: int | None = None,
    error_code: str | None = None,
) -> None:
    """Persist only aggregate operational fields; telemetry must never block planning."""
    usage = usage or {}
    try:
        with SessionLocal() as db:
            db.add(
                AIOperationMetric(
                    occurred_at=datetime.utcnow(),
                    operation=operation,
                    provider=provider,
                    model=model,
                    prompt_version=prompt_version,
                    status=status,
                    latency_ms=latency_ms,
                    input_tokens=int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0),
                    output_tokens=int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0),
                    estimated_cost_usd=max(0.0, float(cost_usd or 0)),
                    accepted_items=accepted_items,
                    rejected_items=rejected_items,
                    error_code=error_code,
                )
            )
            db.commit()
    except Exception:
        logger.exception("ai_metric_write_failed", extra={"operation": operation, "status": status})
