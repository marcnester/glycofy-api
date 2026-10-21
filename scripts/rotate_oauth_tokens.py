"""Validate or re-encrypt OAuth credentials with the primary configured key.

Run against an isolated restore first. Defaults to validation only; --apply
rewrites in one transaction. Never prints credentials, keys, or database URLs.
"""

from __future__ import annotations

import argparse

from cryptography.fernet import InvalidToken
from sqlalchemy import Engine, text

from app.encrypted_types import _PREFIX, _fernet


def rotate_credentials(engine: Engine, *, apply: bool = False) -> dict[str, int]:
    cipher = _fernet()
    accounts = 0
    credentials = 0
    with engine.begin() as connection:
        # Serialize with token refresh/write operations while the rewrite runs.
        if engine.dialect.name == "postgresql":
            connection.execute(text("LOCK TABLE oauth_accounts IN SHARE ROW EXCLUSIVE MODE"))
        rows = connection.execute(text("SELECT id, access_token, refresh_token FROM oauth_accounts")).mappings().all()
        for row in rows:
            values = {}
            for column in ("access_token", "refresh_token"):
                value = row[column]
                if value:
                    if value.startswith(_PREFIX):
                        rotated = cipher.rotate(value[len(_PREFIX) :].encode("ascii")).decode("ascii")
                    else:
                        rotated = cipher.encrypt(value.encode()).decode("ascii")
                    values[column] = _PREFIX + rotated
                    credentials += 1
                else:
                    values[column] = value
            accounts += 1
            if apply:
                connection.execute(
                    text(
                        "UPDATE oauth_accounts SET access_token=:access_token, refresh_token=:refresh_token WHERE id=:id"
                    ),
                    {"id": row["id"], **values},
                )
    return {"accounts": accounts, "credentials": credentials}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from app.config import settings
    from app.db import engine

    if not settings.OAUTH_TOKEN_ENCRYPTION_KEY:
        parser.error("Set an explicit primary encryption key before rotation")
    try:
        result = rotate_credentials(engine, apply=args.apply)
    except (InvalidToken, ValueError, UnicodeError, RuntimeError):
        print("FAIL: A credential cannot be rotated with the configured keys; no changes committed.")
        return 1
    print(
        f"{'Applied' if args.apply else 'Validated'} rotation: {result['accounts']} accounts, {result['credentials']} credentials."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
