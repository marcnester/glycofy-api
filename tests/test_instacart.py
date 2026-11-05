from datetime import date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.config import settings
from app.routers import plans
from app.services.instacart import InstacartError, create_products_link


def test_disabled_flag_blocks_provider_even_with_key(monkeypatch):
    monkeypatch.setattr(settings, "INSTACART_ENABLED", False)
    monkeypatch.setattr(settings, "INSTACART_API_KEY", "synthetic-key")
    with pytest.raises(HTTPException) as exc:
        plans.create_grocery_shopping_link(date(2026, 11, 2), None, None, None)
    assert exc.value.status_code == 404


def test_expired_link_is_not_reused():
    approval = SimpleNamespace(
        shopping_url="https://www.instacart.com/test", shopping_created_at=datetime.utcnow() - timedelta(days=31)
    )
    assert not plans._shopping_link_is_current(approval)
    approval.shopping_created_at = datetime.utcnow()
    assert plans._shopping_link_is_current(approval)


@pytest.mark.parametrize(
    "body", [[], {"products_link_url": "http://instacart.com/test"}, {"products_link_url": "https://evil.example/test"}]
)
def test_provider_rejects_invalid_responses(monkeypatch, body):
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: httpx.Response(200, json=body, request=httpx.Request("POST", a[0]))
    )
    with pytest.raises(InstacartError):
        create_products_link(api_base="https://connect.dev.instacart.tools", api_key="synthetic-key", payload={})


def test_provider_creates_shopping_link_without_ordering(monkeypatch):
    def post(url, **kwargs):
        assert url == "https://connect.dev.instacart.tools/idp/v1/products/products_link"
        assert kwargs["json"]["link_type"] == "shopping_list"
        return httpx.Response(
            200,
            json={"products_link_url": "https://www.instacart.com/store/list/test"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", post)
    assert create_products_link(
        api_base="https://connect.dev.instacart.tools", api_key="synthetic-key", payload={"link_type": "shopping_list"}
    ).startswith("https://www.instacart.com/")
