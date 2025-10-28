from __future__ import annotations

import os
from typing import Any

import jwt  # PyJWT


class _JWTError(RuntimeError):
    pass


def _get_alg_and_key() -> tuple[str, Any, dict[str, bool]]:
    """
    Choose verification mode based on env.
    - If JWT_PUBLIC_KEY is set: verify RS256 using the public key.
    - Else if JWT_SECRET is set: verify HS256 using the shared secret.
    - Else: disable signature verification (DEV ONLY).
    """
    public_key = os.getenv("JWT_PUBLIC_KEY")
    secret = os.getenv("JWT_SECRET")

    if public_key:
        return "RS256", public_key, {"verify_signature": True}
    if secret:
        return "HS256", secret, {"verify_signature": True}
    # Dev fallback (accept unsigned tokens) — do NOT use in prod.
    return "none", "", {"verify_signature": False}


def decode_jwt(token: str) -> dict[str, Any]:
    """
    Decode a JWT and return its payload as a dict.
    Raises _JWTError on failure.
    """
    alg, key, opts = _get_alg_and_key()
    try:
        if opts.get("verify_signature"):
            return jwt.decode(token, key=key, algorithms=[alg], options={"require": ["exp"]}, audience=None)
        # No signature verification (dev only)
        return jwt.decode(token, options={"verify_signature": False, "verify_exp": False})  # type: ignore[no-any-return]
    except Exception as e:
        raise _JWTError(f"jwt_decode_failed: {e}") from e
