# Authentication and session-abuse review — September 9, 2026

## Controls verified

- Passwords require at least 12 characters and are stored with an adaptive
  password hash.
- Known and unknown accounts receive the same login error, and both execute a
  password-hash check to reduce timing-based account enumeration.
- Login, signup, password recovery, reset, OAuth entry points, and verification
  email resends are throttled by client and, where applicable, a privacy-safe
  account key.
- Recovery requests return the same response for existing and missing accounts.
- Password-reset and verification tokens are random, stored only as SHA-256
  hashes, expire, and are single use. Password reset locks token consumption in
  the production database to prevent concurrent replay.
- Password resets and logout increment a server-checked token version, revoking
  every outstanding copy of the prior session.
- Session cookies are HttpOnly, Secure in production, SameSite=Lax, host-only,
  and scoped to `/`. Tokens are never returned in JSON or browser-readable
  storage.
- JWT verification pins HS256 and requires signature, subject, expiry, issued
  time, issuer, audience, and token version.
- Google and Strava OAuth state is signed, expiring, and bound to an HttpOnly
  callback cookie; unsafe return paths are rejected.
- Cross-origin state-changing requests are rejected, request bodies are capped,
  and authentication events are recorded without raw email addresses.

## Scaling boundary

The fixed-window authentication limiter is intentionally process-local while
production runs one web process. Before adding instances or workers, move it to
the configured shared rate-limit backend so limits cannot be multiplied by the
number of processes.

## Ongoing checks

- Alert on sustained authentication failures, reset abuse, OAuth state errors,
  and rate-limit events.
- Re-run these tests after changing identity providers, cookie policy, JWT
  claims, password rules, proxy trust, or deployment topology.
- Do not claim that any review makes account compromise impossible; credential
  reuse, compromised email accounts, user devices, and upstream providers
  remain external risks.
