# Minimal production runtime

The runtime retains Python 3.11 and pinned application dependencies on Debian 13.
A builder assembles only the interpreter, application, required shared libraries,
CA certificates, timezone data, and dash (for Render's predeploy command).
It does not include a package manager, mount tools, Perl, or SQLite support.
Development and host regression tests continue to support SQLite.

`build_rootfs.py` walks retained ELF objects and fails on missing shared libraries.
It preserves the Debian package/version records for copied system libraries and
records their provenance in `/runtime-files.json`. Package CVE records are not
filtered. Trivy scans all severities and blocks every high/critical finding,
including unfixed findings. Full JSON inventory and an SBOM are retained in CI.
The runtime uses UID/GID 65532, with root-owned application files and writable /tmp.

## Verification and rollout

CI must pass application regression tests, CodeQL, dependency and secret audits,
native-library and timezone smoke checks, PostgreSQL 18 migrations, actual image
startup/readiness, and the strict image scan. Run the final image in staging with
production configuration and an isolated PostgreSQL database. Verify login,
session renewal/revocation, passkeys, OAuth encryption, scheduled/background
planning, outbound TLS, and timezone-dependent reconciliation using test accounts.
Do not connect staging to production data or trigger real provider messages.

Observe staging for 24–48 hours, comparing error rates, latency and memory against
baseline. Confirm PostgreSQL TLS and production configuration rejection of SQLite.
Only then promote the same tested image digest. Render's checked-in configuration
uses automatic deploys after main checks pass: merging is a production rollout
step, not simply a review action. This change introduces no schema changes.

Before promotion, record the current Render deployment/image digest. If readiness,
authentication, database connectivity, background jobs, or error/latency regress,
restore that exact deployment, confirm /ready, and rescan the restored image.
Rollback reintroduces the previous image's vulnerability exposure and is temporary.
Do not rebuild a moving base tag as a rollback substitute.

Future Python native extensions must be included by the ELF dependency collector;
non-ELF data/dlopen dependencies need explicit inclusion plus runtime checks.
Rebuild when the pinned base or package updates change and retain full scan counts.

## Build-once release artifact

For an approved release candidate, manually dispatch **Security and tests** on
that exact candidate branch with `publish_runtime=true`. The container job runs
its native-library, PostgreSQL, authentication and vulnerability checks first,
then saves the tested image and its checksum. A separate job, after all four
required jobs pass, loads that saved image and publishes it to GHCR without a
second build. Package-write permission is confined to that manual publication
job; PR builds retain read-only permissions.

The `runtime-image-release-<commit>` artifact records the registry reference as
`ghcr.io/marcnester/glycofy-api@sha256:...`, the local image ID, and the tested
archive checksum. Deploy that digest to staging, finish all integration and
production-mode edge validation, then start the observation window. Any code,
image or material configuration change resets that window. Do not promote the
older Git-built staging deployment by rebuilding its commit.

Production source changes remain a release action: Render's Dashboard source
editor automatically deploys the new source. Do not edit production's source,
merge a change that triggers its automatic deployment, or move the public
hostname until the release gates pass. Update the Blueprint to match the
approved digest-backed deployment as part of that gated promotion. A private
registry image requires a Render pull credential; a public image is suitable
only after the tested image has passed the secret/private-input checks.

The production-mode WebAuthn configuration is bound to `PUBLIC_BASE_URL`:
`WEBAUTHN_RP_ID` must equal its hostname, and `WEBAUTHN_EXPECTED_ORIGIN` must equal
its HTTPS origin. This permits a protected staging hostname to run the same
production security checks. Cloudflare origin authentication, secure cookies,
PostgreSQL, and all other production requirements remain mandatory.
