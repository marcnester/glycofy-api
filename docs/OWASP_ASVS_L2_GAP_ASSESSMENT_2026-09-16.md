# OWASP ASVS 5.0 Level 2 final gap assessment

- Original assessment: September 16, 2026
- Final control review: September 17, 2026
- Application: Glycofy production web application and API
- Target: OWASP Application Security Verification Standard 5.0.0, Level 2
- Owner: Glycofy project owner

## Executive conclusion

All 253 ASVS 5.0 requirements designated Level 1 or Level 2 were reviewed against the current architecture. The authoritative row-level ledger is `OWASP_ASVS_5_L1_L2_MATRIX_2026-09-17.csv`. It is generated from OWASP's official 5.0.0 flat JSON, whose SHA-256 checksum is `8201b20eec2908c3380ac600c91c8ba746346fbb808859366abb232027532311`.

Current disposition:

| Disposition | Controls | Meaning |
| --- | ---: | --- |
| Verified | 178 | Application implementation, regression evidence, or an approved operating control satisfies the requirement. |
| Inherited | 9 | A managed platform control is applicable and documented. |
| Not applicable | 60 | The required technology or protocol is not present in Glycofy. |
| Partial | 6 | A useful control exists, but the exact ASVS wording is not fully met or independently evidenced. |

No applicable control remains wholly open, and this review found no known exploitable critical- or high-severity issue. That statement is bounded by the evidence reviewed and is not a guarantee that no vulnerability exists. OWASP does not certify this assessment, and an independent review remains appropriate before a broad public launch.

## Remediation completed

- Password registration, reset, and change enforce length, strength, application-context, and k-anonymous breached-password screening. Password change requires the current password; reset/change revoke all sessions.
- Google sign-in performs local OIDC ID-token validation for signature, algorithm, key ID, issuer, audience, expiry, issued-at, nonce, subject, and verified email.
- WebAuthn passkeys are available with exact RP/origin validation, required user verification, discoverable credentials, single-use challenges, credential counters, recent-authentication gating, revocation, and security notifications.
- Stateful sessions now use hashed 256-bit identifiers, a five-session limit, sliding idle and absolute lifetimes, device/activity inventory, user revocation, and audited administrative termination.
- Cookies, CSP, anti-caching, CSRF/origin checks, production configuration validation, trusted proxy handling, authorization ownership tests, and security logging are covered by regression tests.
- The security baseline now contains authentication, session, cryptographic, communications, data-classification, vulnerability-remediation, logging, and input/business-rule inventories.
- PostgreSQL transport is required to use TLS in production. Cloudflare-to-Render requests are authenticated with an edge-injected secret header, and direct requests without it are rejected.
- Distributed Redis-backed rate limiting is implemented for future horizontal scale; the present single-process deployment retains a fail-safe local limiter.
- The final review also removed a stored-XSS path in the administrator feedback dashboard by context-escaping every user-influenced field.

## Remaining exact-standard exceptions

These are not known critical/high vulnerabilities. They are explicit gaps between the deployed architecture and the exact ASVS wording.

| Requirement | Disposition | Rationale and next action |
| --- | --- | --- |
| V6.3.3 | Partial, controlled-beta risk acceptance | Passkeys are strongly verified but optional; password and Google remain independent single-factor pathways from Glycofy's perspective. For the invited beta, mitigations include breached-password screening, rate limiting, verified email, stateful-session inventory/revocation, recent-authentication gates, security notifications, and owner monitoring. Require passkey step-up or verified upstream MFA before a broad public launch if strict L2 conformity is the goal. |
| V11.2.2 | Partial | Keys and algorithms are configurable/versioned, but a rehearsed automated bulk re-encryption utility for every encrypted database value is not yet present. Add and exercise a dual-key rotation/re-encryption runbook before a provider-token key rotation is needed. |
| V12.3.4 | Partial, platform constraint | PostgreSQL refuses plaintext transport with `sslmode=require`, but Render's internal certificate is not pinned to a Glycofy-specific CA/certificate. Reassess if Render exposes verified internal-CA configuration or when moving to a database platform that does. |
| V13.2.1 | Partial, provider constraint | OpenAI, USDA, email, and PostgreSQL use provider-issued API keys/passwords rather than short-lived workload identity. Keep least privilege, secret storage, rotation, and revocation; adopt workload identity when providers support it. |
| V13.4.1 | Partial evidence | `.git` is not web-served, but the final runtime artifact needs platform evidence proving source-control metadata is absent from the running service filesystem. Verify after the production deployment or move to a minimal multi-stage image with an explicit copy allowlist. |
| V16.4.3 | Partial | Render logs are outside the web process and alert-class events are delivered to an independent email inbox, but the entire log stream is not exported to an independent SIEM. Add immutable external log export before material scale or regulated operations. |

## Controlled-beta decision

The close-friends beta can proceed after the production checks in `ASVS_EVIDENCE_OPERATIONS.md` pass. The owner accepts the documented V6.3.3 relaxation for this small beta and will not describe Glycofy as independently certified or unconditionally "ASVS compliant." The beta remains one web process, owner-monitored, rapidly patched, and restricted from horizontal scaling until shared worker/rate-limit infrastructure and load testing are complete.

## Evidence and repeatability

- Row-level matrix: `docs/OWASP_ASVS_5_L1_L2_MATRIX_2026-09-17.csv`
- Matrix generator: `scripts/build_asvs_matrix.py`
- Security design: `docs/SECURITY_BASELINE.md`
- Infrastructure evidence: `docs/ASVS_EVIDENCE_OPERATIONS.md`
- Authorization evidence: `docs/AUTHORIZATION_MATRIX.md`
- CI security controls: `.github/workflows/`

The matrix must be regenerated and reviewed after a new authentication pathway, new provider integration, general file upload, second web instance, worker split, mobile application, public launch, material data-model change, or ASVS revision.
