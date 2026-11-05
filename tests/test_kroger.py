from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth_utils import get_current_user
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import KrogerCartReview, KrogerConnection, User
from app.rate_limit import AUTH_LIMITER
from app.routers import kroger as routes
from app.services import kroger


@pytest.fixture
def setup(monkeypatch):
    AUTH_LIMITER.clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add_all(
        [
            User(id=1, email="qfc-test@example.invalid", password_hash="unused"),
            User(id=2, email="other@example.invalid", password_hash="unused"),
        ]
    )
    db.commit()
    monkeypatch.setattr(settings, "KROGER_ENABLED", True)
    monkeypatch.setattr(settings, "KROGER_CLIENT_ID", "synthetic-id")
    monkeypatch.setattr(settings, "KROGER_CLIENT_SECRET", "synthetic-secret")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: db.get(User, 1)
    client = TestClient(app)
    yield client, db
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def test_disabled_no_provider_access(setup, monkeypatch):
    client, _ = setup
    monkeypatch.setattr(settings, "KROGER_ENABLED", False)
    provider = Mock(side_effect=AssertionError("must not call provider"))
    monkeypatch.setattr(kroger, "token_request", provider)
    assert client.get("/v1/qfc/status").json() == {"configured": False, "connected": False, "scope": None}
    assert client.post("/v1/qfc/connect").status_code == 404
    assert client.get("/v1/qfc/stores?zip_code=98133").status_code == 404
    provider.assert_not_called()


def oauth_start(client):
    response = client.post("/v1/qfc/connect", follow_redirects=False)
    assert response.status_code == 200
    params = parse_qs(urlsplit(response.json()["authorize_url"]).query)
    assert params["scope"] == ["cart.basic:write"]
    assert params["redirect_uri"][0].endswith("/oauth/kroger/callback")
    assert "HttpOnly" in response.headers["set-cookie"]
    return params["state"][0]


def test_auth_state_and_encrypted_token(setup, monkeypatch):
    client, db = setup
    exchange = Mock(
        return_value={
            "access_token": "synthetic-access",
            "refresh_token": "synthetic-refresh",
            "expires_in": 3600,
            "scope": kroger.SCOPE,
        }
    )
    monkeypatch.setattr(kroger, "token_request", exchange)
    state = oauth_start(client)
    assert (
        client.get(
            "/oauth/kroger/callback", params={"state": "wrong", "code": "fake"}, follow_redirects=False
        ).status_code
        == 400
    )
    result = client.get("/oauth/kroger/callback", params={"state": state, "code": "fake"}, follow_redirects=False)
    assert result.status_code == 303 and result.headers["location"].endswith("connected")
    raw = db.execute(text("select access_token,refresh_token from kroger_connections")).first()
    assert raw[0].startswith("gfy1:") and "synthetic-access" not in raw[0]
    assert raw[1].startswith("gfy1:") and "synthetic-refresh" not in raw[1]
    assert db.get(KrogerConnection, 1).access_token == "synthetic-access"
    assert (
        client.get(
            "/oauth/kroger/callback", params={"state": state, "code": "fake"}, follow_redirects=False
        ).status_code
        == 400
    )
    exchange.assert_called_once()


def test_state_other_user_expiry_and_denial(setup, monkeypatch):
    client, db = setup
    exchange = Mock(side_effect=AssertionError("must not exchange"))
    monkeypatch.setattr(kroger, "token_request", exchange)
    state = oauth_start(client)
    app.dependency_overrides[get_current_user] = lambda: db.get(User, 2)
    assert (
        client.get(
            "/oauth/kroger/callback", params={"state": state, "code": "fake"}, follow_redirects=False
        ).status_code
        == 400
    )
    app.dependency_overrides[get_current_user] = lambda: db.get(User, 1)
    result = client.get(
        "/oauth/kroger/callback", params={"state": state, "error": "access_denied"}, follow_redirects=False
    )
    assert result.headers["location"].endswith("cancelled")
    state = oauth_start(client)
    monkeypatch.setattr(routes.time, "time", lambda: 9999999999)
    assert (
        client.get(
            "/oauth/kroger/callback", params={"state": state, "code": "fake"}, follow_redirects=False
        ).status_code
        == 400
    )
    exchange.assert_not_called()


def seed_cart(db, monkeypatch):
    db.add(
        KrogerConnection(
            connection_id="test-connection",
            user_id=1,
            access_token="synthetic-access",
            refresh_token="synthetic-refresh",
            expires_at=9999999999,
            scope=kroger.SCOPE,
        )
    )
    db.commit()
    approval = SimpleNamespace(
        id=4,
        approved_at="2026-11-04",
        items=[{"id": "eggs", "name": "Eggs", "pantry": False}, {"id": "oil", "pantry": True}],
        servings=1,
        plan_fingerprint=[{"v": 1}],
    )
    monkeypatch.setattr(routes, "_shopping_approval", lambda *args: (approval, [{"v": 1}]))

    def catalog(path, params=None):
        if path.startswith("/locations/"):
            return {"data": {"locationId": "70500858", "name": "North Seattle", "chain": "QFC"}}
        return {
            "data": {
                "upc": "0001111079769",
                "description": "Eggs",
                "items": [
                    {
                        "soldBy": "UNIT",
                        "size": "12 ct",
                        "fulfillment": {"curbside": True, "delivery": True},
                        "price": {"regular": 4.79},
                    }
                ],
            }
        }

    monkeypatch.setattr(routes, "catalog", catalog)
    return approval


def body():
    return {
        "start": "2026-11-02",
        "end": "2026-11-08",
        "location_id": "70500858",
        "modality": "PICKUP",
        "items": [{"ingredient_id": "eggs", "upc": "0001111079769", "quantity": 2}],
    }


def test_cart_once_and_cross_user(setup, monkeypatch):
    client, db = setup
    seed_cart(db, monkeypatch)
    write = Mock(return_value={})
    monkeypatch.setattr(kroger, "request", write)
    result = client.post("/v1/qfc/cart/preview", json=body())
    assert result.status_code == 200, result.text
    review = result.json()["review_id"]
    assert result.json()["omitted_ingredients"] == 0
    assert client.post(f"/v1/qfc/cart/{review}/send", json={"retailer_store_confirmed": True}).status_code == 200
    assert client.post(f"/v1/qfc/cart/{review}/send", json={"retailer_store_confirmed": True}).status_code == 200
    write.assert_called_once_with(
        "PUT",
        "/cart/add",
        token="synthetic-access",
        json={"items": [{"upc": "0001111079769", "quantity": 2, "modality": "PICKUP"}]},
    )
    assert client.post("/v1/qfc/cart/preview", json=body()).status_code == 409
    db.add(
        KrogerConnection(
            connection_id="other-connection", user_id=2, access_token="other", expires_at=9999999999, scope=kroger.SCOPE
        )
    )
    db.commit()
    app.dependency_overrides[get_current_user] = lambda: db.get(User, 2)
    assert client.post(f"/v1/qfc/cart/{review}/send", json={"retailer_store_confirmed": True}).status_code == 404
    assert write.call_count == 1


def test_uncertain_no_retry(setup, monkeypatch):
    client, db = setup
    seed_cart(db, monkeypatch)
    write = Mock(side_effect=kroger.KrogerError())
    monkeypatch.setattr(kroger, "request", write)
    review = client.post("/v1/qfc/cart/preview", json=body()).json()["review_id"]
    assert client.post(f"/v1/qfc/cart/{review}/send", json={"retailer_store_confirmed": True}).status_code == 502
    assert db.get(KrogerCartReview, review).status == "unknown"
    assert client.post(f"/v1/qfc/cart/{review}/send", json={"retailer_store_confirmed": True}).status_code == 409
    assert client.post("/v1/qfc/cart/preview", json=body()).status_code == 409
    assert write.call_count == 1


def test_stale_approval_expired_and_pantry(setup, monkeypatch):
    client, db = setup
    approval = seed_cart(db, monkeypatch)
    invalid = body()
    invalid["items"][0]["ingredient_id"] = "oil"
    assert client.post("/v1/qfc/cart/preview", json=invalid).status_code == 422
    review = client.post("/v1/qfc/cart/preview", json=body()).json()["review_id"]
    approval.items = [{"id": "changed"}]
    assert client.post(f"/v1/qfc/cart/{review}/send", json={"retailer_store_confirmed": True}).status_code == 409
    approval.items = [{"id": "eggs", "name": "Eggs", "pantry": False}, {"id": "oil", "pantry": True}]
    db.get(KrogerCartReview, review).expires_at = 1
    db.commit()
    assert client.post(f"/v1/qfc/cart/{review}/send", json={"retailer_store_confirmed": True}).status_code == 409


@pytest.mark.parametrize("quantity", [0, -1, 1.5, True, 100, "2"])
def test_invalid_package_counts(setup, quantity):
    client, _ = setup
    payload = body()
    payload["items"][0]["quantity"] = quantity
    assert client.post("/v1/qfc/cart/preview", json=payload).status_code == 422


def test_refresh_and_local_disconnect(setup, monkeypatch):
    client, db = setup
    seed_cart(db, monkeypatch)
    db.get(KrogerConnection, 1).expires_at = 1
    db.commit()
    refresh = Mock(return_value={"access_token": "renewed", "refresh_token": "rotated", "expires_in": 3600})
    monkeypatch.setattr(kroger, "token_request", refresh)
    assert client.post("/v1/qfc/cart/preview", json=body()).status_code == 200
    assert db.get(KrogerConnection, 1).access_token == "renewed"
    assert db.get(KrogerConnection, 1).refresh_token == "rotated"
    assert client.delete("/v1/qfc/connection").status_code == 200
    assert db.get(KrogerConnection, 1) is None


def test_qfc_chain_matches_without_brand_in_name(setup, monkeypatch):
    client, _ = setup
    monkeypatch.setattr(
        routes,
        "catalog",
        lambda *a: {
            "data": [
                {"chain": "QFC", "name": "North Seattle", "locationId": "70500858"},
                {"chain": "FRED", "name": "Other"},
            ]
        },
    )
    result = client.get("/v1/qfc/stores?zip_code=98133").json()
    assert len(result["stores"]) == 1 and result["stores"][0]["name"] == "North Seattle"


def test_client_error_redaction(monkeypatch):
    real = httpx.Client

    def handler(req):
        return httpx.Response(401, json={"error": "sensitive provider content"})

    monkeypatch.setattr(kroger.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(kroger.KrogerError) as error:
        kroger.request("PUT", "/cart/add", token="secret", json={"items": []})
    assert str(error.value) == "Kroger request failed" and error.value.unauthorized


def test_preview_rejects_variable_weight_and_unavailable_package(setup, monkeypatch):
    client, db = setup
    seed_cart(db, monkeypatch)
    base_catalog = routes.catalog

    def weighted(path, params=None):
        result = base_catalog(path, params)
        if path.startswith("/products/"):
            result["data"]["items"][0]["soldBy"] = "WEIGHT"
        return result

    monkeypatch.setattr(routes, "catalog", weighted)
    assert client.post("/v1/qfc/cart/preview", json=body()).status_code == 422
    assert db.query(KrogerCartReview).count() == 0


def test_migration_upgrade_and_downgrade():
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect

    spec = importlib.util.spec_from_file_location("qfc_migration", Path("alembic/versions/add_kroger_cart.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        module.upgrade()
        assert "kroger_cart_reviews" in inspect(conn).get_table_names()
        module.downgrade()
        assert "kroger_cart_reviews" not in inspect(conn).get_table_names()
    engine.dispose()


def test_reconnect_invalidates_pending_cart_review(setup, monkeypatch):
    client, db = setup
    seed_cart(db, monkeypatch)
    write = Mock(return_value={})
    monkeypatch.setattr(kroger, "request", write)
    review = client.post("/v1/qfc/cart/preview", json=body()).json()["review_id"]
    db.get(KrogerConnection, 1).connection_id = "new-customer-authorization"
    db.commit()
    assert client.post(f"/v1/qfc/cart/{review}/send", json={"retailer_store_confirmed": True}).status_code == 409
    write.assert_not_called()


@pytest.mark.parametrize(
    "path", ["/products/https://evil.invalid", "//evil.invalid", "/products/../cart/add", "/locations/70500858?x=1"]
)
def test_provider_path_injection_rejected(path):
    with pytest.raises(kroger.KrogerError):
        kroger.request("GET", path, token="synthetic")


@pytest.mark.parametrize("confirmation", [None, False])
def test_cart_requires_explicit_retailer_store_confirmation(setup, monkeypatch, confirmation):
    client, db = setup
    seed_cart(db, monkeypatch)
    write = Mock(return_value={})
    monkeypatch.setattr(kroger, "request", write)
    review = client.post("/v1/qfc/cart/preview", json=body()).json()["review_id"]
    payload = {} if confirmation is None else {"retailer_store_confirmed": confirmation}
    assert client.post(f"/v1/qfc/cart/{review}/send", json=payload).status_code == 422
    write.assert_not_called()
    assert db.get(KrogerCartReview, review).status == "preview"


def test_product_coverage_uses_approved_amount_and_preview_rechecks_package_count(setup, monkeypatch):
    client, db = setup
    approval = seed_cart(db, monkeypatch)
    approval.items[0].update(quantity=18, unit="oz")
    product = {
        "upc": "0001111079769",
        "description": "Plain cod fillets",
        "items": [{"soldBy": "UNIT", "size": "12 oz", "fulfillment": {"curbside": True}}],
    }

    def catalog(path, params=None):
        if path.startswith("/locations/"):
            return {"data": {"name": "Northgate", "chain": "QFC"}}
        return {"data": [product] if path == "/products" else product}

    monkeypatch.setattr(routes, "catalog", catalog)
    query = {
        "location_id": "70500858",
        "term": "cod",
        "start": "2026-11-02",
        "end": "2026-11-08",
        "ingredient_id": "eggs",
    }
    result = client.get("/v1/qfc/products", params=query)
    assert result.status_code == 200
    assert result.json()["products"][0]["coverage"]["suggested_packages"] == 2
    selected = body()
    selected["items"][0]["quantity"] = 1
    result = client.post("/v1/qfc/cart/preview", json=selected)
    assert result.status_code == 200
    assert result.json()["items"][0]["coverage"]["status"] == "short"
    assert result.json()["items"][0]["coverage"]["shortfall"] == 6
    assert client.get("/v1/qfc/products", params={**query, "ingredient_id": "oil"}).status_code == 422
    assert client.get("/v1/qfc/products", params={**query, "ingredient_id": "unknown"}).status_code == 422
    approval.plan_fingerprint = []
    assert client.get("/v1/qfc/products", params=query).status_code == 409
