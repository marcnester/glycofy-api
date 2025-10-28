import os
import time

from jose import JWTError, jwt

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ISS = os.getenv("JWT_ISS", "glyco.local")
JWT_AUD = os.getenv("JWT_AUD", "glyco.web")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
ID_COOKIE_NAME = os.getenv("ID_COOKIE_NAME", "id_token")

LOGIN_EMAIL = os.getenv("LOGIN_EMAIL")  # optional (dev)
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD")  # optional (dev)


def _now() -> int:
    return int(time.time())


def _mint_jwt(sub: str, email: str, name: str, minutes: int) -> str:
    n = _now()
    payload = {
        "sub": sub,
        "email": email,
        "name": name,
        "roles": ["user"],
        "iat": n,
        "nbf": n - 5,
        "iss": JWT_ISS,
        "aud": JWT_AUD,
        "exp": n + minutes * 60,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def mint_dev_jwt(sub="user_123", email="marc@example.com", name="Marc Nester") -> str:
    return _mint_jwt(sub, email, name, ACCESS_TOKEN_EXPIRE_MINUTES)


def mint_login_jwt(email: str, remember: bool = False) -> str:
    name = email.split("@")[0].replace(".", " ").title() if "@" in email else "User"
    minutes = (60 * 24 * 30) if remember else ACCESS_TOKEN_EXPIRE_MINUTES
    sub = f"user:{email}"
    return _mint_jwt(sub, email, name, minutes)


def verify_jwt(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISS,
            audience=JWT_AUD,
            options={"leeway": 60},
        )
    except JWTError as e:
        raise ValueError(str(e))


def get_bearer_token(authorization_header: str | None) -> str | None:
    if not authorization_header:
        return None
    parts = authorization_header.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def extract_token(authorization_header: str | None, cookie_token: str | None) -> str | None:
    return get_bearer_token(authorization_header) or cookie_token


def validate_credentials(email: str, password: str) -> bool:
    # For dev:
    # If LOGIN_EMAIL/PASSWORD are set, enforce exact match.
    # Otherwise accept any non-empty values.
    if LOGIN_EMAIL or LOGIN_PASSWORD:
        if LOGIN_EMAIL and email != LOGIN_EMAIL:
            return False
        if LOGIN_PASSWORD and password != LOGIN_PASSWORD:
            return False
        return True
    return bool(email) and bool(password)
