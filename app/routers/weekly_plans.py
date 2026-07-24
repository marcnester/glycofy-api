# app/routers/weekly_plans.py
from __future__ import annotations

from datetime import date, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.models import User
from app.routers.auth import get_current_user

router = APIRouter(
    prefix="/v1/plan",
    tags=["plans-weekly"],
)


def _bool_param(value: bool) -> str:
    """Render a bool as lower-case string for query params."""
    return "true" if value else "false"


@router.post("/week/generate")
async def generate_weekly_plan(
    request: Request,
    start: date | None = Query(
        None,
        description="Start date for the generated week (defaults to today).",
    ),
    days: int = Query(
        7,
        ge=1,
        le=14,
        description="How many consecutive days to generate (1–14).",
    ),
    engine: str = Query(
        "llm",
        description="Plan engine to use for each day (e.g., 'llm' or 'heuristic').",
    ),
    replace: bool = Query(
        False,
        description="If true, existing plans for those days may be replaced.",
    ),
    user: User = Depends(get_current_user),
):
    """
    Generate a block of daily plans for the current user, typically a full week.

    This endpoint does NOT implement new plan logic itself; instead, it
    orchestrates calls to your existing:

        POST /v1/plan/{date}?engine=...&replace=...

    for each day in the requested range.

    That way, all your current heuristic / AI logic and preference handling
    stays in one place.
    """
    if start is None:
        start = date.today()

    if days < 1:
        raise HTTPException(status_code=400, detail="days must be >= 1")

    base_url = str(request.base_url).rstrip("/")

    # Forward auth headers + cookies so the inner POST calls run
    # as the same user.
    fwd_headers = {}
    auth_header = request.headers.get("authorization")
    if auth_header:
        fwd_headers["authorization"] = auth_header

    # Optional: forward a simple User-Agent so logs are readable
    fwd_headers["user-agent"] = f"GlycofyWeeklyPlanner/1.0 (+user_id={user.id})"

    results: list[dict] = []

    async with httpx.AsyncClient(
        base_url=base_url,
        headers=fwd_headers,
        cookies=request.cookies,
        timeout=30.0,
    ) as client:
        for offset in range(days):
            day = start + timedelta(days=offset)
            path = f"/v1/plan/{day.isoformat()}?engine={engine}&replace={_bool_param(replace)}"

            try:
                resp = await client.post(path)
            except Exception as exc:  # network or internal error
                results.append(
                    {
                        "date": day.isoformat(),
                        "ok": False,
                        "status": None,
                        "error": f"request_failed: {exc}",
                    }
                )
                continue

            ok = 200 <= resp.status_code < 300
            entry = {
                "date": day.isoformat(),
                "ok": ok,
                "status": resp.status_code,
            }

            ctype = resp.headers.get("content-type", "")
            if not ok:
                # Try to capture a concise error payload for debugging
                if ctype.startswith("application/json"):
                    try:
                        body = resp.json()
                    except Exception:
                        body = None
                    if isinstance(body, dict) and "detail" in body:
                        entry["error"] = body["detail"]
                    else:
                        entry["error"] = body
                else:
                    entry["error"] = resp.text[:500]
            results.append(entry)

    if not any(r["ok"] for r in results):
        # All days failed → treat as a hard error
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": "Weekly generation failed for all days",
                "start": start.isoformat(),
                "days": days,
                "engine": engine,
                "replace": replace,
                "results": results,
            },
        )

    return {
        "start": start.isoformat(),
        "days": days,
        "engine": engine,
        "replace": replace,
        "user_id": user.id,
        "results": results,
    }
