"""Feature-gated QFC catalog, customer consent, and reviewed cart handoff."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from datetime import date
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user
from app.config import settings
from app.db import get_db
from app.models import KrogerAuthorization, KrogerCartReview, KrogerConnection, User
from app.rate_limit import AUTH_LIMITER
from app.routers.plans import _shopping_approval
from app.services import kroger

router = APIRouter(tags=["qfc"])
COOKIE = "__Host-kroger-state" if settings.is_production else "kroger_state"


def configured() -> bool:
    return bool(settings.KROGER_ENABLED and settings.KROGER_CLIENT_ID and settings.KROGER_CLIENT_SECRET)


def enabled(user: User = Depends(get_current_user)):
    if not configured():
        raise HTTPException(404, "QFC shopping is not enabled")
    AUTH_LIMITER.check(f"qfc:{user.id}", maximum=120, window_seconds=600)


def callback_uri() -> str:
    return str(settings.PUBLIC_BASE_URL).rstrip("/") + "/oauth/kroger/callback"


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def approval_snapshot(db, user, start, end):
    if end < start or (end - start).days > 30:
        raise HTTPException(422, "Choose a shopping range of at most 31 days")
    approval, fingerprint = _shopping_approval(start, end, db, user)
    if not approval or approval.plan_fingerprint != fingerprint:
        raise HTTPException(409, "Review and approve the current grocery list first")
    return {
        "approval_id": approval.id,
        "approved_at": str(approval.approved_at),
        "plan_fingerprint": fingerprint,
        "items": approval.items,
        "servings": approval.servings,
    }


def connection(db, user):
    row = db.query(KrogerConnection).filter_by(user_id=user.id).with_for_update().first()
    if not row or kroger.SCOPE not in row.scope.split():
        raise HTTPException(409, "Connect your QFC account first")
    if row.expires_at <= int(time.time()) + 90:
        if not row.refresh_token:
            raise HTTPException(409, "Reconnect your QFC account")
        try:
            result = kroger.token_request({"grant_type": "refresh_token", "refresh_token": row.refresh_token})
            scope = result.get("scope", row.scope)
            if not isinstance(scope, str) or kroger.SCOPE not in scope.split():
                raise kroger.KrogerError()
            row.access_token = result["access_token"]
            row.refresh_token = result.get("refresh_token") or row.refresh_token
            row.scope = scope
            row.expires_at = int(time.time()) + result["expires_in"]
            db.commit()
        except kroger.KrogerError as exc:
            if exc.unauthorized:
                db.delete(row)
                db.commit()
            raise HTTPException(409, "QFC authorization could not be renewed; reconnect") from None
    return row.access_token


def catalog(path, params=None):
    try:
        return kroger.request("GET", path, token=kroger.catalog_token(), params=params)
    except kroger.KrogerError:
        raise HTTPException(502, "QFC catalog is temporarily unavailable") from None


def require_qfc(location_id):
    result = catalog("/locations/" + location_id).get("data", {})
    if not isinstance(result, dict) or result.get("chain") != "QFC":
        raise HTTPException(422, "Select a QFC store")
    return result


@router.get("/v1/qfc/status")
def status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(KrogerConnection, user.id) if configured() else None
    return {"configured": configured(), "connected": bool(row), "scope": kroger.SCOPE if row else None}


@router.post("/v1/qfc/connect", dependencies=[Depends(enabled)])
def connect(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    state = secrets.token_urlsafe(32)
    db.add(
        KrogerAuthorization(
            state_hash=digest(state), user_id=user.id, expires_at=int(time.time()) + 600, consumed=False
        )
    )
    db.commit()
    authorize_url = (
        kroger.API
        + "/connect/oauth2/authorize?"
        + urlencode(
            {
                "client_id": settings.KROGER_CLIENT_ID,
                "redirect_uri": callback_uri(),
                "response_type": "code",
                "scope": kroger.SCOPE,
                "state": state,
            }
        )
    )
    response = JSONResponse({"authorize_url": authorize_url})
    response.set_cookie(
        COOKIE, state, max_age=600, httponly=True, secure=settings.is_production, samesite="lax", path="/"
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/oauth/kroger/callback", dependencies=[Depends(enabled)])
def callback(
    request: Request,
    state: str = Query(..., max_length=200),
    code: str | None = Query(None, max_length=2048),
    error: str | None = Query(None, max_length=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    cookie = request.cookies.get(COOKIE, "")
    if not cookie or not secrets.compare_digest(state, cookie):
        raise HTTPException(400, "Invalid or expired QFC authorization; connect again")
    claimed = (
        db.query(KrogerAuthorization)
        .filter(
            KrogerAuthorization.state_hash == digest(state),
            KrogerAuthorization.user_id == user.id,
            KrogerAuthorization.consumed.is_(False),
            KrogerAuthorization.expires_at > int(time.time()),
        )
        .update({"consumed": True}, synchronize_session=False)
    )
    db.commit()
    if not claimed:
        raise HTTPException(400, "Invalid or expired QFC authorization; connect again")
    outcome = "cancelled"
    if not error and code:
        try:
            result = kroger.token_request(
                {"grant_type": "authorization_code", "code": code, "redirect_uri": callback_uri()}
            )
            scope = result.get("scope", kroger.SCOPE)
            if not isinstance(scope, str) or kroger.SCOPE not in scope.split():
                raise kroger.KrogerError()
            row = db.get(KrogerConnection, user.id) or KrogerConnection(user_id=user.id)
            row.connection_id = str(uuid.uuid4())
            row.access_token = result["access_token"]
            row.refresh_token = result.get("refresh_token")
            row.expires_at = int(time.time()) + result["expires_in"]
            row.scope = scope
            db.add(row)
            db.commit()
            outcome = "connected"
        except kroger.KrogerError:
            outcome = "failed"
    response = RedirectResponse("/ui/qfc.html?connection=" + outcome, status_code=303)
    response.delete_cookie(COOKIE, path="/", secure=settings.is_production, httponly=True, samesite="lax")
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@router.delete("/v1/qfc/connection", dependencies=[Depends(enabled)])
def disconnect(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    db.query(KrogerConnection).filter_by(user_id=user.id).delete()
    db.commit()
    return {"connected": False, "message": "Local connection removed. Manage app permission in your Kroger account."}


@router.get("/v1/qfc/stores", dependencies=[Depends(enabled)])
def stores(zip_code: str = Query(..., pattern=r"^\d{5}$"), user: User = Depends(get_current_user)):
    body = catalog("/locations", {"filter.zipCode.near": zip_code, "filter.radiusInMiles": 10, "filter.limit": 200})
    return {
        "stores": [
            {k: row.get(k) for k in ("locationId", "name", "address")}
            for row in body.get("data", [])
            if row.get("chain") == "QFC"
        ]
    }


@router.get("/v1/qfc/products", dependencies=[Depends(enabled)])
def products(
    location_id: str = Query(..., pattern=r"^\d{8}$"),
    term: str = Query(..., min_length=2, max_length=100),
    user: User = Depends(get_current_user),
):
    require_qfc(location_id)
    body = catalog("/products", {"filter.locationId": location_id, "filter.term": term, "filter.limit": 10})
    return {
        "products": [
            {k: row.get(k) for k in ("upc", "productId", "description", "brand", "items")}
            for row in body.get("data", [])
        ]
    }


class CartItem(BaseModel):
    ingredient_id: str = Field(min_length=1, max_length=200)
    upc: str = Field(pattern=r"^\d{8,14}$")
    quantity: int = Field(strict=True, ge=1, le=99)


class CartPreview(BaseModel):
    start: date
    end: date
    location_id: str = Field(pattern=r"^\d{8}$")
    modality: Literal["PICKUP", "DELIVERY"] = "PICKUP"
    items: list[CartItem] = Field(min_length=1, max_length=100)


@router.post("/v1/qfc/cart/preview", dependencies=[Depends(enabled)])
def preview(body: CartPreview, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    connection(db, user)
    approved = approval_snapshot(db, user, body.start, body.end)
    allowed = {str(item["id"]) for item in approved["items"] if not item.get("pantry")}
    ids = [item.ingredient_id for item in body.items]
    if len(set(ids)) != len(ids) or not set(ids).issubset(allowed):
        raise HTTPException(422, "Select each grocery ingredient once; exclude pantry items")
    if len({item.upc for item in body.items}) != len(body.items):
        raise HTTPException(422, "Combine quantities for duplicate products before sending")
    store = require_qfc(body.location_id)
    selected = []
    for item in body.items:
        product = catalog("/products/" + item.upc, {"filter.locationId": body.location_id}).get("data", {})
        if not isinstance(product, dict) or product.get("upc") != item.upc:
            raise HTTPException(422, "Product unavailable; search again")
        packages = product.get("items", [])
        if len(packages) != 1:
            raise HTTPException(422, "Product package is ambiguous; choose a different product")
        package = packages[0]
        fulfillment = "curbside" if body.modality == "PICKUP" else "delivery"
        if package.get("soldBy") != "UNIT" or not package.get("fulfillment", {}).get(fulfillment):
            raise HTTPException(422, "Choose a packaged product available for the selected fulfillment method")
        selected.append(
            {
                **item.model_dump(),
                "description": product.get("description"),
                "size": package.get("size"),
                "price": package.get("price"),
            }
        )
    snapshot = {
        "start": str(body.start),
        "end": str(body.end),
        "location_id": body.location_id,
        "store": store.get("name"),
        "modality": body.modality,
        "items": selected,
        "approval": approved,
        "connection_id": db.get(KrogerConnection, user.id).connection_id,
    }
    # A stable fingerprint prevents duplicate writes even across different tabs/reviews.
    key = digest(
        {
            "user": user.id,
            "connection_id": snapshot["connection_id"],
            "approval": approved,
            "store": body.location_id,
            "modality": body.modality,
            "items": sorted([i.model_dump() for i in body.items], key=lambda x: x["ingredient_id"]),
        }
    )
    row = db.query(KrogerCartReview).filter_by(fingerprint=key).first()
    if row:
        if row.status != "preview":
            raise HTTPException(
                409, "This selection was already sent or its outcome is uncertain. Check your QFC cart."
            )
        row.snapshot = snapshot
        row.expires_at = int(time.time()) + 600
    else:
        row = KrogerCartReview(
            id=str(uuid.uuid4()),
            user_id=user.id,
            fingerprint=key,
            snapshot=snapshot,
            status="preview",
            expires_at=int(time.time()) + 600,
        )
        db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Review already exists; refresh before continuing") from None
    return {
        "review_id": row.id,
        "store": snapshot["store"],
        "items": selected,
        "modality": body.modality,
        "expires_at": row.expires_at,
        "omitted_ingredients": len(allowed - set(ids)),
        "notice": "Review package counts. Prices and availability can change. Checkout and store selection remain on QFC.",
    }


@router.post("/v1/qfc/cart/{review_id}/send", dependencies=[Depends(enabled)])
def send(review_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    token = connection(db, user)
    row = db.query(KrogerCartReview).filter_by(id=review_id, user_id=user.id).first()
    if not row:
        raise HTTPException(404, "Review not found")
    if row.status == "sent":
        return {"status": "sent", "cart_url": "https://www.qfc.com/cart"}
    snap = row.snapshot
    if snap["connection_id"] != db.get(KrogerConnection, user.id).connection_id:
        raise HTTPException(409, "QFC account connection changed; review products again")
    if (
        approval_snapshot(db, user, date.fromisoformat(snap["start"]), date.fromisoformat(snap["end"]))
        != snap["approval"]
    ):
        raise HTTPException(409, "Your grocery list changed; review again")
    claimed = (
        db.query(KrogerCartReview)
        .filter(
            KrogerCartReview.id == review_id,
            KrogerCartReview.status == "preview",
            KrogerCartReview.expires_at > int(time.time()),
        )
        .update({"status": "sending"}, synchronize_session=False)
    )
    db.commit()
    if not claimed:
        raise HTTPException(409, "Review expired, already sending, or outcome uncertain. Check your QFC cart.")
    try:
        kroger.request(
            "PUT",
            "/cart/add",
            token=token,
            json={
                "items": [
                    {"upc": item["upc"], "quantity": item["quantity"], "modality": snap["modality"]}
                    for item in snap["items"]
                ]
            },
        )
    except kroger.KrogerError:
        db.query(KrogerCartReview).filter_by(id=review_id).update({"status": "unknown"})
        db.commit()
        raise HTTPException(
            502, "QFC did not confirm the result. Check your cart before making another selection; do not retry."
        ) from None
    db.query(KrogerCartReview).filter_by(id=review_id).update({"status": "sent"})
    db.commit()
    return {"status": "sent", "cart_url": "https://www.qfc.com/cart"}


@router.get("/v1/qfc/groceries", dependencies=[Depends(enabled)])
def groceries(start: date, end: date, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    snapshot = approval_snapshot(db, user, start, end)
    return {"items": [item for item in snapshot["items"] if not item.get("pantry")]}
