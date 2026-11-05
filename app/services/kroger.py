"""Server-only Kroger public API client. Never retry cart writes automatically."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings

API = "https://api.kroger.com/v1"
SCOPE = "cart.basic:write"


class KrogerError(RuntimeError):
    def __init__(self, *, unauthorized: bool = False):
        super().__init__("Kroger request failed")
        self.unauthorized = unauthorized


def request(method: str, path: str, *, token: str | None = None, **kwargs) -> dict[str, Any]:
    if path not in {"/connect/oauth2/token", "/cart/add", "/locations", "/products"} and not re.fullmatch(
        r"/(?:locations/[0-9]{8}|products/[0-9]{8,14})", path
    ):
        raise KrogerError()
    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            response = client.request(method, API + quote(path, safe="/"), headers=headers, **kwargs)
        if response.status_code not in (200, 201, 204):
            raise KrogerError(unauthorized=response.status_code in (401, 403))
        if response.status_code == 204:
            return {}
        body = response.json()
        if not isinstance(body, dict):
            raise KrogerError()
        return body
    except (httpx.HTTPError, ValueError, TypeError):
        raise KrogerError() from None


def token_request(data: dict[str, str]) -> dict[str, Any]:
    result = request(
        "POST",
        "/connect/oauth2/token",
        auth=(settings.KROGER_CLIENT_ID, settings.KROGER_CLIENT_SECRET),
        data=data,
    )
    if (
        not isinstance(result.get("access_token"), str)
        or not result["access_token"]
        or str(result.get("token_type", "")).lower() != "bearer"
    ):
        raise KrogerError()
    try:
        expires = int(result["expires_in"])
        if not 0 < expires <= 86400:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise KrogerError() from None
    result["expires_in"] = expires
    return result


def catalog_token() -> str:
    return token_request({"grant_type": "client_credentials", "scope": "product.compact"})["access_token"]
