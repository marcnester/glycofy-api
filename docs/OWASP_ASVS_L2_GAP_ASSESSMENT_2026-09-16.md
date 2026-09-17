# OWASP ASVS 5.0 Level 2 gap assessment

- Assessment date: September 16, 2026
- Application: Glycofy production web application and API
- Target: OWASP Application Security Verification Standard 5.0.0, Level 2

## Executive conclusion

Glycofy has a strong security baseline for a small, controlled beta. This
review did not identify a known exploitable critical- or high-severity
vulnerability. The focused security suite passed, dependency and static scans
reported no findings at their configured thresholds, production rejected
unauthenticated access and unsupported methods, and the Render origin remained
unreachable through its default hostname.

Glycofy **cannot yet claim OWASP ASVS Level 2 conformance**. The principal
product gap is multi-factor authentication for users of the email/password
path. Other material gaps are password screening and authenticated password
change, user-visible session management, stronger validation of Google identity
assertions, and several exact browser and data-protection requirements. A
separate group of gaps is documentary or infrastructure evidence rather than a
demonstrated exploitable flaw.

This is a gap assessment, not an OWASP certification, guarantee of security, or
substitute for an independent penetration test.

## Scope and method

The assessment reviewed all 253 requirements marked Level 1 or Level 2 in the
official [ASVS 5.0.0 release](https://github.com/OWASP/ASVS/tree/v5.0.0). Evidence
included source code, automated tests, CI workflows, deployment configuration,
security and operations documentation, earlier two-user authorization and DAST
results, and low-impact production checks.

Controls were treated as:

- **Verified** when current code, a test, production evidence, or a sufficiently
  specific operating control demonstrated the requirement.
- **Partial** when a useful control exists but does not meet the exact ASVS
  requirement or the production configuration evidence is incomplete.
- **Open** when the requirement is applicable and the required control is
  absent.
- **Not applicable** when Glycofy does not use the technology or role described
  by the requirement.

The assessment did not inspect secret values, production database contents,
Cloudflare or Render account audit logs, or provider-internal controls. Controls
that require those facts remain partial until evidence is captured.

## Evidence collected

### Automated and production checks

- 73 focused authentication, authorization, hardening, edge, account-trust, and
  incident-response tests passed.
- Bandit completed with no high-severity result.
- `pip-audit` reported no known vulnerable Python dependencies.
- `npm audit --omit=dev --audit-level=high` reported zero vulnerabilities.
- Production returned the configured HSTS, CSP, `nosniff`, frame, referrer,
  permissions, COOP, and CORP headers.
- Unauthenticated `/users/me` returned `401`; HTTP `TRACE` returned `405`.
- The disabled Render hostname returned a blocked `404`, preventing a simple
  Cloudflare bypass.

### Existing control evidence

- Owner-scoped queries and cross-account `404` behavior are covered by the
  authorization matrix and regression suite.
- Production refuses unsafe secrets, insecure cookies, debug mode, SQLite,
  unrestricted origins or hosts, and invalid scaling configuration.
- Session tokens pin algorithm, issuer, audience, expiry, issued-at time, token
  type, and user token version. Logout and password reset revoke existing
  sessions.
- Reset and verification tokens are random, hashed, expiring, and single-use.
- Unsafe cookie-authenticated requests require an allowed origin; CORS and host
  validation are restrictive.
- OAuth provider credentials are encrypted at rest in the application database.
- Structured, correlated, privacy-reduced security logs and audit events are
  emitted; alert-class events are delivered out of band.
- Container builds are digest-pinned, patched, and run as a non-root user.
- CI includes tests, migrations, Ruff, Bandit, dependency audit, secret scanning,
  CodeQL, SBOM generation, and fixable high/critical container scanning.
- Data export, provider revocation, account deletion, backup/restore drills, and
  an incident-response runbook are implemented.

## Open and partial requirements

Priorities below describe the order for ASVS alignment. They do not assert that
each item is independently exploitable at the same severity.

| ID | Priority | ASVS requirement(s) | Status | Gap and evidence | Recommended disposition |
| --- | --- | --- | --- | --- | --- |
| ASVS-01 | P1 | V6.3.3 | Open | Google users may protect their Google account with MFA, but Glycofy neither verifies that strength nor requires a second factor for its email/password path. | Implement passkeys/WebAuthn or another phishing-resistant factor and recovery flow. Until then, document that Glycofy is not ASVS L2 conformant and encourage Google sign-in for the controlled beta. |
| ASVS-02 | P1 | V6.1.2, V6.2.2–V6.2.4, V6.2.11–V6.2.12 | Open/partial | Passwords are at least 12 characters, allow broad composition, are adaptively hashed, and can be reset by verified email. There is no authenticated current-password change flow and no common, context-specific, or breached-password rejection. | Add current-plus-new password change, a local top-password/context list, breached-password screening with a privacy-preserving lookup or maintained offline corpus, and regression tests. |
| ASVS-03 | P1 | V7.1.2, V7.1.3, V7.5.2 | Open/partial | Idle and absolute lifetimes are documented and global revocation works through `token_version`. Stateless sessions are not individually inventoried, displayed, limited, or revoked, and the Google/Glycofy session relationship is not fully documented. | Store hashed session records with device/time metadata, enforce a documented concurrent-session policy, add re-authenticated session review/revocation, and document federated logout/lifetime behavior. |
| ASVS-04 | P1 | V6.8.2, V6.8.4 | Partial | Google authorization code, state, verified email, fixed provider endpoints, and TLS are used. Authentication currently relies on Google's user-info response rather than locally validating a signed ID token, nonce, issuer, audience, and authentication claims through a maintained OIDC library. | Complete the flow as OIDC using a maintained validator, nonce binding, and explicit issuer/audience/signature checks; document assumed authentication strength when `acr`/`amr` is unavailable. |
| ASVS-05 | P2 | V3.3.1, V3.3.3 | Partial | Session cookies are host-only, `Secure`, `HttpOnly`, `SameSite=Lax`, and path `/`, but their production names do not use the required `__Host-` prefix. | Rename every host-only security cookie with `__Host-`, preserve path `/`, omit `Domain`, and test attributes in production. |
| ASVS-06 | P2 | V3.4.3 | Partial | The global CSP is restrictive and includes `object-src 'none'`, but uses `base-uri 'self'` instead of ASVS L2's required `base-uri 'none'`. | Change to `base-uri 'none'` and rerun the UI and production-header suite. |
| ASVS-07 | P2 | V14.3.1, V14.3.2 | Partial | Authentication and UI HTML responses use `Cache-Control: no-store`; sensitive authenticated JSON responses do not uniformly receive it. Logout clears cookies but does not send `Clear-Site-Data` or otherwise provide explicit browser-data clearing evidence. | Apply `no-store` to authenticated/sensitive responses and clear relevant browser caches/storage on logout without deleting unrelated origin data. |
| ASVS-08 | P2 | V11.1.1–V11.1.2, V13.1.1, V14.1.1–V14.1.2, V15.1.1, V16.1.1 | Open documentation | Strong implementation fragments exist, but there is no single approved cryptographic inventory/lifecycle, communications inventory, data classification and handling matrix, vulnerability-remediation SLA, or full logging inventory. | Publish concise versioned policies with owners, review dates, key rotation/re-encryption procedures, provider/data flows, retention, access, and severity-based remediation deadlines. |
| ASVS-09 | P2 | V12.3.1–V12.3.4, V13.2.1–V13.2.2 | Partial/unverified | External provider traffic uses certificate-validating HTTPS and PostgreSQL has no public allowlist. Evidence was not captured that every Render-internal database connection is encrypted, nor that all backend authentication uses short-lived or certificate credentials as ASVS prefers over static API keys and database passwords. | Capture Render/PostgreSQL TLS settings and least-privilege grants; enable verified database TLS if absent; inventory unavoidable static provider keys and define rotation. |
| ASVS-10 | P2 | V2.4.1, V6.3.1, V15.1.3, V15.2.2 | Partial | Costly planning is asynchronous, durable, bounded, cancellable, and has per-process quotas. Authentication and job limiting remain process-local, so controls are not reliable after horizontal scaling. | Before a second web instance or wider launch, move rate limits and jobs to shared infrastructure, enforce per-user/global quotas, and load test recovery and quota exhaustion. |
| ASVS-11 | P2 | V15.3.4 | Partial/unverified | Uvicorn trusts forwarded headers from all immediate peers. The default Render hostname is blocked and Cloudflare fronts production, but evidence has not established that untrusted clients can never influence the effective client address used for rate limits and audit hashes. | Restrict trusted proxy hops where the platform permits, normalize the Cloudflare client address at one trusted boundary, and add spoofed-forwarded-header production tests. |
| ASVS-12 | P2 | V13.3.1–V13.3.2, V16.4.2–V16.4.3 | Partial/unverified | Render stores deployment secrets and platform logs outside the container; application logs are not written locally. Account-level evidence for secret-access least privilege, log immutability, retention, and independent alerting was outside this review. | Record owner-only/RBAC settings, log retention and tamper controls, alert destinations, and quarterly access review evidence. Prefer an independent log destination before material scale. |
| ASVS-13 | P3 | V2.1.1–V2.1.3, V2.3.2, V6.1.1, V6.1.3, V6.3.4 | Partial documentation | Pydantic schemas, domain validators, rate limits, tests, and auth-flow code provide implementation evidence, but validation/business limits and all authentication pathways are not maintained as one authoritative specification. | Add an input/business-rule inventory and consolidate email, Google, reset, verification, session, throttling, and recovery expectations in the security baseline. |

## Not-applicable control families

The following controls are out of scope for the current architecture and must be
reassessed if the corresponding technology is introduced:

- LDAP, XPath, XML/XSLT, deserialization of untrusted native objects, JNDI,
  LaTeX, and SOAP processing.
- GraphQL, WebSocket, WebRTC, and client-side browser extensions/plugins.
- User-supplied executable archives, remotely served uploads, and general file
  storage. The only upload is a size-bounded, schema-validated TrainingPeaks
  CSV which is not persisted or served as a file.
- OAuth authorization-server, consent-screen, dynamic-client-registration, and
  OpenID Provider controls. Glycofy is an OAuth/OIDC client, not a provider.
- SMS, TOTP, lookup-secret, and other MFA-mechanism controls until Glycofy
  implements those mechanisms. Once passkeys are implemented, their WebAuthn
  lifecycle and recovery requirements become applicable.

## Recommended sequence

### Before claiming ASVS Level 2

1. Deliver passkeys/MFA and recovery (ASVS-01).
2. Add password change and password screening (ASVS-02).
3. Implement individual session inventory and revocation (ASVS-03).
4. Complete standards-based OIDC validation (ASVS-04).
5. Close the cookie, CSP, and anti-caching deltas (ASVS-05 through ASVS-07).
6. Approve the crypto, communications, data, dependency, and logging policies
   and collect the missing Render/Cloudflare evidence (ASVS-08, ASVS-09,
   ASVS-11, and ASVS-12).
7. Replace process-local coordination and complete load testing before scaling
   beyond one web process (ASVS-10).
8. Rerun the complete 253-control matrix and commission an independent review
   before making a public conformity claim.

### Acceptable controlled-beta posture

For an invited beta of close friends and relatives, the current posture is
reasonable if the owner accepts that it is not yet ASVS L2 conformant, keeps the
beta small, watches alerts, keeps the origin and database private, uses one web
process, rapidly patches dependencies, and does not make an ASVS certification
claim. Account MFA remains the most important planned improvement; the existing
Google sign-in path benefits from users' Google security settings but does not
prove or enforce MFA to Glycofy.

## Reassessment triggers

Reassess this document after any authentication or session change, new provider
integration, general file upload, second web instance, background-worker split,
mobile application, public launch, material data-model change, or major ASVS
revision. Review open evidence items quarterly even if code does not change.
