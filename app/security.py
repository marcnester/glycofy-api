# app/security.py
from passlib.context import CryptContext

# Accept both bcrypt_sha256 and bcrypt; default to bcrypt_sha256 for new hashes
pwd_context = CryptContext(
    schemes=["bcrypt_sha256", "bcrypt"],
    default="bcrypt_sha256",
    deprecated="auto",
)

def hash_password(password: str) -> str:
    """
    Hash a plaintext password using the default scheme.
    """
    return pwd_context.hash(password)

def verify_password(plain_password: str, password_hash: str) -> bool:
    """
    Verify a plaintext password against a stored hash. Returns True/False.
    """
    try:
        return pwd_context.verify(plain_password, password_hash)
    except Exception:
        # If hash format is unknown/corrupt, treat as invalid
        return False