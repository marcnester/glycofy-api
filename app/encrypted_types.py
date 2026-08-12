from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import settings

_PREFIX = "gfy1:"


def _fernet() -> Fernet:
    configured = (settings.OAUTH_TOKEN_ENCRYPTION_KEY or "").strip()
    if configured:
        try:
            return Fernet(configured.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise RuntimeError("OAUTH_TOKEN_ENCRYPTION_KEY must be a valid Fernet key") from exc

    # Local development remains zero-config. Production rejects a missing key
    # in Settings before the application can start.
    digest = hashlib.sha256(f"glycofy-dev:{settings.JWT_SECRET}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


class EncryptedText(TypeDecorator):
    """Encrypt secrets at the ORM boundary while allowing legacy plaintext reads."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None or value == "":
            return value
        if value.startswith(_PREFIX):
            return value
        encrypted = _fernet().encrypt(value.encode()).decode("ascii")
        return _PREFIX + encrypted

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None or value == "" or not value.startswith(_PREFIX):
            return value
        try:
            return _fernet().decrypt(value[len(_PREFIX) :].encode("ascii")).decode()
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise RuntimeError("Unable to decrypt stored OAuth credential") from exc
