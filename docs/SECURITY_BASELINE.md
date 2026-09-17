# Glycofy security baseline

- Owner: Glycofy project owner
- Approved: September 17, 2026
- Review cadence: quarterly and after a material authentication, provider, data-model, or infrastructure change
- Scope: production web application, API, PostgreSQL, Cloudflare, Render, OpenAI, USDA FoodData Central, Strava, email delivery, and optional grocery handoff

This document is the authoritative security design reference. Implementation evidence is linked in the ASVS matrix and CI configuration.

## Authentication and recovery

Glycofy supports password, Google OpenID Connect, and WebAuthn passkey authentication. Passwords are 12–128 characters, adaptively hashed with bcrypt, screened for strength and application/account context, and checked using the k-anonymous Pwned Passwords range API. Password change requires the current password. Reset links are random, stored only as SHA-256 hashes, expire after one hour, and are single-use.

Google login validates signed ID tokens for algorithm, key identifier, signature, issuer, audience, expiry, issued-at time, subject, verified email, and nonce. Account linking prefers Google's immutable subject and rejects conflicting identities. Glycofy does not claim that Google authentication used MFA unless Google supplies a validated assurance claim.

Passkeys use WebAuthn with an exact relying-party ID and HTTPS origin, required discoverable credentials, and required user verification. Challenges contain 256 bits of randomness, expire after five minutes, and are single-use. Glycofy stores the credential public key and counter; biometric and private-key material remain on the authenticator. Adding or removing a passkey requires authentication within five minutes and produces an email security notification. Verified-email password recovery and Google sign-in remain recovery paths.

### Controlled-beta authentication exception

ASVS V6.3.3 expects MFA or a combination of independent authentication mechanisms. During the invited close-friends beta, passkeys are strongly encouraged but not mandatory; email/password and Google remain valid independent sign-in methods. This is a time-bounded usability decision for a small owner-monitored beta, not a claim that every session is MFA-authenticated. Compensating controls are verified email, breached-password screening, layered per-account/per-client throttling, generic authentication errors, stateful-session limits and revocation, recent-authentication gates for sensitive changes, security notifications, privacy-reduced security auditing, and prompt owner response. The exception must be reconsidered before a broad public launch, material increase in sensitivity, or regulatory scope.

## Session policy

- Idle timeout: 24 hours, sliding after authenticated activity.
- Absolute timeout: seven days from authentication.
- Maximum active sessions: five per user. Creating a sixth revokes the oldest.
- Each session has a random 256-bit identifier; only its SHA-256 hash is stored.
- Users can review device label, authentication method, creation time, and last activity, and can revoke individual or all other sessions after recent authentication.
- Expired or revoked session records are retained for 30 days for security review and then removed opportunistically. Used or expired WebAuthn challenges are removed after 24 hours.
- Logout revokes only the current Glycofy session. Password reset/change revokes all Glycofy sessions. Google logout is independent: ending a Glycofy session does not end the user's Google session, and ending Google does not revoke an already-issued Glycofy session.
- Administrative session termination is permitted only to the configured operations administrators and is security-audited.

## Cryptographic inventory and lifecycle

| Purpose | Mechanism | Storage | Rotation / response |
| --- | --- | --- | --- |
| Browser session signing | HS256 with a random key of at least 256 bits | Render secret | Rotate immediately on exposure; rotation invalidates all sessions. Planned migration to asymmetric signing before multiple independent issuers. |
| Password storage | bcrypt via Passlib, cost 12 or stronger current policy | PostgreSQL hash only | Rehash on successful login when policy changes. |
| OAuth tokens at rest | Fernet-compatible authenticated encryption with dedicated key | Render secret + encrypted DB value | Rotate using decrypt-old/re-encrypt-new maintenance procedure; revoke provider tokens on suspected exposure. |
| Account action and session IDs | CSPRNG values; SHA-256 hashes stored | Hash in PostgreSQL | Single-use/expiry; revoke rows on security events. |
| WebAuthn | Platform authenticator public-key credentials, user verification required | Public key and counter in PostgreSQL | User or admin revocation; private keys never held by Glycofy. |
| Transport | TLS at Cloudflare/public edge; TLS-required private PostgreSQL connection | Platform managed | Track platform TLS posture and certificate lifecycle. |

Cryptographic algorithms are supplied by maintained platform or library implementations. Custom encryption algorithms are prohibited. Secrets must never be committed, logged, emailed, placed in client JavaScript, or copied into issue trackers. Compromise response is documented in `INCIDENT_RESPONSE.md`.

## Communications and trust boundaries

| Flow | Data | Control |
| --- | --- | --- |
| Browser → Cloudflare → Render | Account, profile, training, meal-plan requests | HTTPS, HSTS, Cloudflare WAF/rate controls, authenticated edge-to-origin header, strict Host/CORS/origin checks |
| Render → PostgreSQL | Application records and encrypted provider tokens | Same-region private network plus `sslmode=require`, least-privilege application user |
| Render → OpenAI | Minimized athlete context, targets, preferences, exclusions; no credentials | Certificate-validating HTTPS, provider key in Render secret store |
| Render → USDA FDC | Food-description queries | Certificate-validating HTTPS; no account or health identifiers |
| Render ↔ Strava | OAuth and training activity data | Certificate-validating HTTPS, OAuth state/PKCE-equivalent provider controls, encrypted tokens, least scopes |
| Render → email provider | Recipient email and transactional/security content | TLS, secret-held credentials; meal, health, and training details excluded |
| Browser → grocery provider | User-approved grocery handoff | Explicit user action; provider URL and payload constraints |

New outbound hosts or data categories require a data-flow and privacy review before release.

## Data classification and handling

| Class | Examples | Handling |
| --- | --- | --- |
| Restricted | Password hashes, OAuth tokens, signing/encryption keys | No client exposure; encryption or one-way hashing as appropriate; owner-only production access; never logged |
| Sensitive | Profile biometrics, diet/allergies, training, meal plans, feedback | Owner-scoped authorization; no public caching; minimized provider transfer; export/deletion supported |
| Internal | Security events, request IDs, aggregate operations metrics | Access limited to operations administrators; privacy-reduced; retention-controlled |
| Public | Static UI, public legal documents, health liveness response | May be cached where explicitly allowed; no sensitive state |

Production data is not copied to developer devices. Support and security notifications use request IDs rather than health, meal, or activity content. Data retention periods are defined in configuration and operations documentation.

## Vulnerability management

CI blocks failed tests, migration-chain failures, lint/type failures, committed secrets, fixable high/critical container findings, and known vulnerable direct Python or npm runtime dependencies. The owner reviews security alerts and dependency updates.

Remediation targets begin at confirmation: critical actively exploitable issues within 24 hours, high within 7 days, medium within 30 days, and low within 90 days. If the target cannot be met, the affected feature is disabled or isolated and the exception, compensating controls, owner, and expiry are recorded. Internet-facing authentication and authorization defects are never accepted without a time-bounded mitigation.

## Logging and monitoring inventory

| Event | Destination | Retention | Data rule |
| --- | --- | --- | --- |
| Request/access logs | Render platform logs | Platform setting, reviewed quarterly | Request ID, method, route, status, duration; no bodies/tokens |
| Security audit events | PostgreSQL | 365 days | Event type/outcome/severity, pseudonymous client hash, user ID when necessary |
| Alert-class events | Independent email inbox | Mailbox retention policy | Request ID and event metadata only; no credentials or health details |
| AI operations metrics | PostgreSQL | 90 days | Latency, outcome, token/cost aggregates; no prompt or meal content |
| Product/beta analytics | PostgreSQL | 180/365 days | Minimized product events; no health details |

Logs must not contain passwords, session IDs, OAuth credentials, passkey assertions, full request bodies, or medical/training details. Operations access is restricted to configured administrators and reviewed quarterly. Alert delivery is tested after configuration changes.

## Input and business-rule inventory

Pydantic schemas define API types and length/range constraints. Request bodies are capped at 1 MiB. Training CSV data is parsed as data, schema-validated, bounded, and never served as an uploaded file. Identifiers are server-resolved under the authenticated owner; clients cannot select a user ID. Dates, meal counts, snack counts, durations, units, macro ranges, provider URLs, redirect paths, and job concurrency all have explicit bounds in schemas or domain services. SQLAlchemy parameterization is mandatory; dynamic SQL, shell construction from user input, unsafe deserialization, and server-side template evaluation are prohibited.

Changes to these limits require tests for minimum, maximum, malformed, duplicate, cross-user, and resource-exhaustion cases.
