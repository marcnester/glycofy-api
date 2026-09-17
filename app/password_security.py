from __future__ import annotations

import hashlib

import httpx
from zxcvbn import zxcvbn

from app.config import settings

PWNED_PASSWORDS_RANGE_URL = "https://api.pwnedpasswords.com/range/{prefix}"
_CONTEXT_WORDS = ("glycofy", "glycofy.ai", "athlete", "meal planner", "mealplan")


class PasswordPolicyError(ValueError):
    """Raised when a proposed password does not satisfy Glycofy's policy."""


class PasswordScreenUnavailable(RuntimeError):
    """Raised when production cannot securely complete breach screening."""


def _context_inputs(email: str | None) -> list[str]:
    values = list(_CONTEXT_WORDS)
    if email:
        normalized = email.strip().casefold()
        values.extend((normalized, normalized.split("@", 1)[0]))
    return [value for value in values if value]


def _is_breached(password: str) -> bool:
    digest = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    response = httpx.get(
        PWNED_PASSWORDS_RANGE_URL.format(prefix=prefix),
        headers={"Add-Padding": "true", "User-Agent": "Glycofy-Password-Screen"},
        timeout=settings.PASSWORD_BREACH_CHECK_TIMEOUT_SECONDS,
        follow_redirects=False,
    )
    response.raise_for_status()
    for row in response.text.splitlines():
        candidate, _, count = row.partition(":")
        if candidate.strip().upper() == suffix:
            return int(count.strip() or "0") > 0
    return False


def validate_new_password(password: str, *, email: str | None = None) -> None:
    """Reject weak/contextual passwords and, in production, known breached values."""
    if len(password) < 12 or len(password) > 128:
        raise PasswordPolicyError("Use a password between 12 and 128 characters.")

    context_inputs = _context_inputs(email)
    folded_password = password.casefold()
    if any(value.casefold() in folded_password for value in _CONTEXT_WORDS):
        raise PasswordPolicyError("Choose a password that does not contain your account or Glycofy terms.")

    result = zxcvbn(password, user_inputs=context_inputs)
    if int(result.get("score", 0)) < 3:
        raise PasswordPolicyError("Choose a less common password that is harder to guess.")

    if settings.PASSWORD_BREACH_CHECK_ENABLED:
        try:
            breached = _is_breached(password)
        except (httpx.HTTPError, ValueError) as exc:
            raise PasswordScreenUnavailable("Password security screening is temporarily unavailable.") from exc
        if breached:
            raise PasswordPolicyError("Choose a password that has not appeared in a known data breach.")
