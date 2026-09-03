from __future__ import annotations

from urllib.parse import urlsplit

import httpx


class InstacartError(RuntimeError):
    pass


def create_products_link(*, api_base: str, api_key: str, payload: dict) -> str:
    try:
        response = httpx.post(
            f"{api_base.rstrip('/')}/idp/v1/products/products_link",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=20.0,
        )
        response.raise_for_status()
        url = str(response.json().get("products_link_url") or "")
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise InstacartError("Instacart could not create the shopping link") from exc
    host = (urlsplit(url).hostname or "").lower()
    if urlsplit(url).scheme != "https" or not (host == "instacart.com" or host.endswith(".instacart.com")):
        raise InstacartError("Instacart returned an invalid shopping link")
    return url
