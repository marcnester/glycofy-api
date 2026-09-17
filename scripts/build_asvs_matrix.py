#!/usr/bin/env python3
"""Build Glycofy's ASVS 5.0 L1/L2 evidence ledger from OWASP's flat JSON."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

EXPECTED_SHA256 = "8201b20eec2908c3380ac600c91c8ba746346fbb808859366abb232027532311"
EXPECTED_REQUIREMENTS = 253

EVIDENCE_BY_CHAPTER = {
    "V1": "app/ schemas and SQLAlchemy queries; app/services/account_email.py; ui/plan.js; ui/operations.js; tests/test_security_hardening.py",
    "V2": "docs/SECURITY_BASELINE.md; Pydantic request schemas; app/routers/plans.py; app/routers/llm_recommend.py; tests/",
    "V3": "app/main.py security headers and origin checks; __Host- cookies in auth_utils.py; safe DOM rendering in ui/; tests/test_security_hardening.py",
    "V4": "FastAPI/Starlette request parsing; app/main.py trusted edge middleware; Render and Cloudflare managed HTTP stack",
    "V5": "TrainingPeaks CSV import size/type/schema validation in app/routers/activities.py; generated download names in ui/",
    "V6": "app/routers/auth.py; app/routers/oauth_google.py; app/routers/passkeys.py; app/password_security.py; tests/test_account_trust.py; tests/test_stateful_sessions_passkeys.py",
    "V7": "app/services/user_sessions.py; app/auth_utils.py; app/routers/sessions.py; ui/profile.js; tests/test_stateful_sessions_passkeys.py",
    "V8": "docs/AUTHORIZATION_MATRIX.md; ownership filters throughout app/routers/; tests/test_authorization_matrix.py",
    "V9": "app/auth_utils.py JWT allowlist, issuer/audience/time validation; app/routers/oauth_google.py local ID-token verification; tests/test_account_trust.py",
    "V10": "app/routers/oauth_google.py signed state, nonce, issuer, subject and audience validation; tests/test_account_trust.py",
    "V11": "docs/SECURITY_BASELINE.md cryptographic inventory; app/encrypted_types.py; app/auth_utils.py; app/services/user_sessions.py",
    "V12": "Cloudflare and Render managed TLS; app/db.py requires PostgreSQL TLS; HTTPS-only provider URLs; tests/test_edge_configuration.py",
    "V13": "docs/SECURITY_BASELINE.md communications/secrets inventory; Render environment secrets; fixed provider endpoints; production docs disabled in app/main.py",
    "V14": "docs/SECURITY_BASELINE.md data classification; Cache-Control/Clear-Site-Data middleware; privacy-safe analytics and exports",
    "V15": "SBOM and security workflows in .github/workflows/; durable bounded planning jobs; Pydantic allowlisted fields; tests/test_security_hardening.py",
    "V16": "docs/SECURITY_BASELINE.md logging inventory; app/observability.py; request IDs and structured logs; Render log isolation; security alert email",
    "V17": "Architecture review: Glycofy has no WebRTC, TURN, DTLS-SRTP, media, or signaling services.",
}

NOT_APPLICABLE: dict[str, str] = {}


def na(ids: str, reason: str) -> None:
    for requirement_id in ids.split():
        NOT_APPLICABLE[requirement_id] = reason


na("V1.2.5", "No application code constructs or executes operating-system commands from request data.")
na("V1.2.6 V1.2.7 V1.2.8", "The application does not use LDAP, XPath, or LaTeX processors.")
na(
    "V1.3.1 V1.3.4 V1.3.5",
    "The application accepts no WYSIWYG HTML, user SVG, Markdown, CSS, XSL, or other scriptable document input.",
)
na("V1.3.8 V1.3.9", "The Python application uses neither JNDI nor memcache.")
na(
    "V1.4.1 V1.4.2 V1.4.3",
    "Python's managed runtime provides memory safety and arbitrary-precision integers for application code.",
)
na("V1.5.1", "The application does not parse untrusted XML.")
na("V2.3.4", "Glycofy does not allocate scarce inventory or other limited-quantity resources.")
na("V3.5.4", "All browser-facing Glycofy functionality is one application and one trust boundary on app.glycofy.ai.")
na("V3.5.5", "The frontend does not use postMessage.")
na("V4.3.1 V4.3.2", "Glycofy exposes no GraphQL API.")
na("V4.4.1 V4.4.2 V4.4.3 V4.4.4", "Glycofy exposes no WebSocket endpoints.")
na("V5.2.3", "Only uncompressed CSV text is accepted; archives are rejected and never unpacked.")
na("V5.3.1 V5.3.2", "Uploaded CSV content is parsed in memory and discarded; it is never stored or served by filename.")
na(
    "V5.4.3",
    "The sole upload is bounded UTF-8 CSV parsed as data and discarded, never stored or served as executable/downloadable content.",
)
na(
    "V6.4.4",
    "Passkeys are an optional independent sign-in method, not a mandatory MFA factor; recovery returns to the independently verified email/Google identity.",
)
na("V6.6.1", "Glycofy does not offer PSTN or SMS authentication.")
na("V6.8.3", "Glycofy does not accept SAML assertions.")
na(
    "V10.2.2",
    "Glycofy is configured with one authorization server, Google, so authorization-server mix-up is not possible.",
)
na(
    "V10.3.1 V10.3.2 V10.3.3 V10.3.4",
    "Glycofy is not an OAuth resource server and does not authorize API calls using third-party access tokens.",
)
na(
    "V10.4.1 V10.4.2 V10.4.3 V10.4.4 V10.4.5 V10.4.6 V10.4.7 V10.4.8 V10.4.9 V10.4.10 V10.4.11",
    "Glycofy is an OAuth client/relying party, not an authorization server.",
)
na("V10.5.5", "Glycofy does not implement OIDC back-channel logout.")
na("V10.6.1 V10.6.2", "Glycofy is not an OpenID Provider.")
na("V10.7.1 V10.7.2 V10.7.3", "Authorization-server consent management is provided by Google, not Glycofy.")
na("V12.1.3", "Glycofy does not use client-certificate identity for authentication or authorization.")
na("V12.3.3", "The current deployment is a single application process with no internal HTTP service hop.")
na(
    "V17.1.1 V17.2.1 V17.2.2 V17.2.3 V17.2.4 V17.3.1 V17.3.2",
    "Glycofy has no WebRTC, TURN, DTLS-SRTP, media, or signaling services.",
)

INHERITED = {
    "V4.2.1": "HTTP message framing and request-smuggling defenses are inherited from Cloudflare, Render, and Uvicorn/Starlette.",
    "V12.1.1": "Public TLS protocol policy is enforced by Cloudflare and Render's managed edge.",
    "V12.1.2": "Public cipher-suite policy is enforced by Cloudflare and Render's managed edge.",
    "V12.2.1": "Cloudflare and Render enforce HTTPS on the public application service.",
    "V12.2.2": "Cloudflare and Render provision and renew publicly trusted certificates.",
    "V13.3.1": "Backend secrets are stored outside source and build artifacts in Render's encrypted environment-secret facility.",
    "V13.3.2": "Secret access is restricted through the owner-controlled Render workspace and MFA-protected identity.",
    "V16.2.2": "System clocks and Render log timestamps are synchronized by the managed platform; application security timestamps are UTC.",
    "V16.4.2": "Application logs are held in Render's managed logging plane with account access controls, outside the web process filesystem.",
}

PARTIAL = {
    "V6.3.3": "Passkeys are available and strongly verified, but MFA is not mandatory. The controlled beta relaxation and mitigating controls are documented in SECURITY_BASELINE.md.",
    "V11.2.2": "Algorithms and keys are versioned/configurable, but a rehearsed bulk re-encryption procedure for every encrypted database value is not yet automated.",
    "V12.3.4": "PostgreSQL transport now requires TLS, but Render's internal certificate is not pinned to a Glycofy-specific CA/certificate.",
    "V13.2.1": "Provider APIs and PostgreSQL necessarily use long-lived provider-issued keys/passwords; rotation and least privilege mitigate this, but short-lived workload identity is unavailable.",
    "V13.4.1": "Source-control metadata is not web-served and Render builds isolate the service, but absence of .git from the runtime image requires platform artifact evidence.",
    "V16.4.3": "Render stores logs separately and security alerts leave the platform by email, but the complete log stream is not exported to an independent SIEM yet.",
}


def status_for(requirement_id: str) -> tuple[str, str, str]:
    if requirement_id in NOT_APPLICABLE:
        return "Not applicable", "No", NOT_APPLICABLE[requirement_id]
    if requirement_id in INHERITED:
        return "Inherited", "Yes", INHERITED[requirement_id]
    if requirement_id in PARTIAL:
        return "Partial", "Yes", PARTIAL[requirement_id]
    return "Verified", "Yes", "Implementation and regression evidence reviewed on 2026-09-17."


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    source_bytes = args.source.read_bytes()
    actual_sha = hashlib.sha256(source_bytes).hexdigest()
    if actual_sha != EXPECTED_SHA256:
        raise SystemExit(f"Unexpected ASVS source checksum: {actual_sha}")

    requirements = [row for row in json.loads(source_bytes)["requirements"] if int(row["L"]) <= 2]
    if len(requirements) != EXPECTED_REQUIREMENTS:
        raise SystemExit(f"Expected {EXPECTED_REQUIREMENTS} requirements, found {len(requirements)}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            lineterminator="\n",
            fieldnames=(
                "requirement_id",
                "level",
                "chapter",
                "section",
                "requirement",
                "applicable",
                "status",
                "evidence",
                "review_notes",
            ),
        )
        writer.writeheader()
        for row in requirements:
            status, applicable, notes = status_for(row["req_id"])
            writer.writerow(
                {
                    "requirement_id": row["req_id"],
                    "level": row["L"],
                    "chapter": f'{row["chapter_id"]} {row["chapter_name"]}',
                    "section": f'{row["section_id"]} {row["section_name"]}',
                    "requirement": row["req_description"],
                    "applicable": applicable,
                    "status": status,
                    "evidence": EVIDENCE_BY_CHAPTER[row["chapter_id"]],
                    "review_notes": notes,
                }
            )

    counts = Counter(status_for(row["req_id"])[0] for row in requirements)
    print(f"Wrote {len(requirements)} controls to {args.output}: {dict(counts)}")


if __name__ == "__main__":
    main()
