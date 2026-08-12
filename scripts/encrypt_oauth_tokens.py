"""One-time migration of legacy plaintext OAuth credentials.

Configure OAUTH_TOKEN_ENCRYPTION_KEY first, back up the database, then run:
    python -m scripts.encrypt_oauth_tokens
"""

from sqlalchemy import text

from app.db import engine
from app.encrypted_types import EncryptedText


def main() -> None:
    encrypted = EncryptedText()
    updated = 0
    with engine.begin() as connection:
        rows = connection.execute(text("SELECT id, access_token, refresh_token FROM oauth_accounts")).mappings()
        for row in rows:
            values: dict[str, str | None] = {}
            for column in ("access_token", "refresh_token"):
                value = row[column]
                if value and not value.startswith("gfy1:"):
                    values[column] = encrypted.process_bind_param(value, connection.dialect)
            if values:
                connection.execute(
                    text(
                        "UPDATE oauth_accounts SET access_token = :access_token, refresh_token = :refresh_token "
                        "WHERE id = :id"
                    ),
                    {
                        "id": row["id"],
                        "access_token": values.get("access_token", row["access_token"]),
                        "refresh_token": values.get("refresh_token", row["refresh_token"]),
                    },
                )
                updated += 1
    print(f"Encrypted OAuth credentials for {updated} account(s).")


if __name__ == "__main__":
    main()
