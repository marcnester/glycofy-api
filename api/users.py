# api/users.py
import os
import sqlite3
import time

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()

# ----- Settings from env -----
DB_URL = os.getenv("DATABASE_URL", "sqlite:///./glycofy.db")
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret")
JWT_ALG = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ISS = os.getenv("JWT_ISS", None)
JWT_AUD = os.getenv("JWT_AUD", None)
ID_COOKIE_NAME = os.getenv("ID_COOKIE_NAME", "id_token")


def _sqlite_path_from_url(url: str) -> str:
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "", 1)
    if url.startswith("sqlite://"):
        return url.replace("sqlite://", "", 1)
    # Not SQLite? This helper only supports SQLite flows for MVP.
    raise RuntimeError("DATABASE_URL must be sqlite:// for this users router.")


DB_PATH = _sqlite_path_from_url(DB_URL)


# ----- Auth helper (decode cookie-based JWT) -----
def current_user(request: Request):
    tok = request.cookies.get(ID_COOKIE_NAME)
    if not tok:
        raise HTTPException(status_code=401, detail="Missing auth cookie")
    try:
        options = {"verify_signature": True, "verify_exp": True}
        kwargs = {"algorithms": [JWT_ALG]}
        if JWT_ISS:
            kwargs["issuer"] = JWT_ISS
        if JWT_AUD:
            kwargs["audience"] = JWT_AUD
        claims = jwt.decode(tok, JWT_SECRET, **kwargs, options=options)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")
    # Minimal claims we rely on
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Token missing sub")
    return {
        "sub": sub,
        "email": claims.get("email"),
        "name": claims.get("name"),
    }


# ----- DB helpers -----
def _ensure_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_prefs (
            user_sub   TEXT PRIMARY KEY,
            name       TEXT,
            timezone   TEXT,
            diet_pref  TEXT,
            updated_at INTEGER
        )
        """
    )


def _merge_user_with_prefs(user_claims, row):
    # Defaults if nothing stored yet
    out = {
        "sub": user_claims["sub"],
        "email": user_claims.get("email"),
        "name": user_claims.get("name") or "",
        "timezone": "America/Los_Angeles",
        "diet_pref": "omnivore",
        "roles": ["user"],
    }
    if row:
        out["name"] = row[1] if row[1] is not None else out["name"]
        out["timezone"] = row[2] if row[2] is not None else out["timezone"]
        out["diet_pref"] = row[3] if row[3] is not None else out["diet_pref"]
    return out


# ----- Schemas -----
class UserUpdate(BaseModel):
    name: str | None = None
    timezone: str | None = None
    diet_pref: str | None = None


# ----- ROUTES -----
@router.put("/users/me")
def update_me(payload: UserUpdate, user=Depends(current_user)):
    """
    Upsert name / timezone / diet_pref into SQLite user_prefs keyed by user_sub.
    Returns merged profile (JWT claims overlaid with saved prefs).
    """
    try:
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        _ensure_table(cur)

        # Read existing
        cur.execute(
            "SELECT user_sub, name, timezone, diet_pref, updated_at FROM user_prefs WHERE user_sub = ?;",
            (user["sub"],),
        )
        row = cur.fetchone()

        if row is None:
            cur.execute(
                """
                INSERT INTO user_prefs (user_sub, name, timezone, diet_pref, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user["sub"], payload.name, payload.timezone, payload.diet_pref, int(time.time())),
            )
        else:
            # Use COALESCE-like logic in Python to keep previous values if None
            new_name = payload.name if payload.name is not None else row[1]
            new_tz = payload.timezone if payload.timezone is not None else row[2]
            new_diet = payload.diet_pref if payload.diet_pref is not None else row[3]
            cur.execute(
                """
                UPDATE user_prefs
                SET name = ?, timezone = ?, diet_pref = ?, updated_at = ?
                WHERE user_sub = ?
                """,
                (new_name, new_tz, new_diet, int(time.time()), user["sub"]),
            )

        con.commit()
        cur.execute(
            "SELECT user_sub, name, timezone, diet_pref, updated_at FROM user_prefs WHERE user_sub = ?;",
            (user["sub"],),
        )
        row2 = cur.fetchone()
        return _merge_user_with_prefs(user, row2)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"save_failed: {e}")
    finally:
        try:
            con.close()
        except Exception:
            pass
