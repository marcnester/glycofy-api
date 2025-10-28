# api/google_oauth.py
import os
import urllib.parse

import httpx

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URL = os.getenv("GOOGLE_REDIRECT_URL")  # e.g., http://127.0.0.1:8090/oauth/google/callback

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


def configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URL)


def start_url(state: str = "glyco"):
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URL,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "include_granted_scopes": "true",
        "state": state,
        "prompt": "consent",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


async def exchange_code_for_tokens(code: str):
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": GOOGLE_REDIRECT_URL,
                "grant_type": "authorization_code",
            },
        )
        r.raise_for_status()
        return r.json()


async def validate_id_token(id_token: str):
    # Validate via tokeninfo endpoint (confirms aud/exp/etc.)
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(TOKENINFO_URL, params={"id_token": id_token})
        r.raise_for_status()
        return r.json()
