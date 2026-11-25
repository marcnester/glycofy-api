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

## Temporary zlib security rebuild

Debian 13 currently lacks the zlib fix for CVE-2026-76844 (also tracked as
CVE-2026-85091). Rebuild Debian source version `1:1.3.dfsg+really1.3.2-4`
against the existing trixie toolchain. The dedicated builder enables only a
`deb-src` sid repository, verifies source archive SHA256 values, and runs Debian's
package build/tests. No sid binary repository or operating-system upgrade is added.
Only the rebuilt zlib1g package enters the application builder; compiler and source
files do not enter the runtime. Preserve its actual Debian package metadata for
SBOM and vulnerability scans. No CVE ignore or severity exception is used.

CI verifies the installed package version, Python compression round trips and
repeated failed `gzwrite` calls against `/dev/full`, including `gzclearerr`.
Remove this temporary source rebuild once trixie provides the patched package;
retest and rescan the resulting exact artifact. Authoritative status:
https://security-tracker.debian.org/tracker/CVE-2026-76844
Upstream fix: https://github.com/madler/zlib/commit/df84af25dc1942490e1d1c899a07619152a46148

The stable-release scanner feed reports this source-rebuilt version as affected
because trixie has no fixed-version entry. CI therefore generates an OpenVEX
`fixed` statement only after the native regression checks pass. It is scoped to
the exact zlib package version, epoch, architecture and distro, and records the
actual library SHA256 and source patch. It does not match the old package. The
strict gate consumes this fixed-status evidence; the raw all-severity inventory
runs without VEX and is retained alongside the statement, so findings remain
auditable. All other high/critical findings continue to block publication.

## Temporary OpenSSL security backport

The recovery release encountered CVE-2026-124709 in Debian 13's OpenSSL 3.5.9.
The [upstream advisory](https://openssl-library.org/news/secadv/20261117.txt)
provides the public fix before the scheduled 3.5.10 release. The OpenSSL builder
rebuilds Debian source `3.5.9-1~deb13u1` with upstream 3.5-branch commit
[`f3acb0a3328046741479482c51cec815839e1b05`](https://github.com/openssl/openssl/commit/f3acb0a3328046741479482c51cec815839e1b05).
Source archives, patch and the resulting patched source file are SHA256-pinned.
The backport uses its own `3.5.9-1~deb13u1+glycofy1` package version; it does not
claim to be the unreleased upstream version. Debian's package build and test
suite must pass before its two runtime packages can leave the build stage.

`check_openssl.py` records the patched source and installed library hashes during
the image build. CI verifies that exact manifest against the runtime libraries,
package metadata, TLS 1.3 support and CA certificate loading before emitting an
OpenVEX `fixed` statement scoped to this exact package, architecture and distro.
The gate consumes the merged, verified zlib/OpenSSL fix evidence. Raw scanner
findings and both individual statements remain in the retained inventory;
other vulnerabilities still block publication. Remove this temporary rebuild
and its fixed-status evidence when Debian provides the upstream fix, then
repeat native, PostgreSQL, outbound TLS and staging tests on the new artifact.

## November 23 QUIC backport and scanner database consistency

CVE-2026-107494 is a separate OpenSSL QUIC denial-of-service issue. Glycofy uses
Uvicorn over TCP behind managed TLS proxies, and its Python HTTP clients do not
use OpenSSL's native QUIC API. That limits the identified application path, but
is not a claim about Render's host kernel or managed proxies. The library is
patched rather than relying on an unreachability exception.

The `+glycofy2` OpenSSL package retains the CVE-2026-124709 fix and adds upstream
3.5-branch commit `734c6cd7444bdbf589bfec917e47598c1b21ddd3`, plus its upstream
QUIC retransmission regression `42919a7f4e00aa217fce1532fbeb49f21be2ee7b`.
Both patch downloads are checksum-pinned. The package build runs Debian's full
test suite, including the QUIC regression. Build verification checks the patched
source and test fingerprints; runtime verification checks the exact package and
loaded library hashes before emitting narrowly scoped fixed-status evidence.
No other CVE is waived.

Every container job downloads the current Trivy database into a unique run and
attempt directory, with no restored vulnerability database cache. Both the raw
inventory and blocking scan use that directory with updates disabled. Database
and metadata checksums are checked after both scans and retained with the raw
inventory, gate output, database publication metadata and scanner version.
This prevents a green blocking scan based on an older cache than the inventory.
The weekly job rebuilds main; release publication retains and deploys its exact
validated image digest. A later weekly build is not proof that an older deployed
digest has been rescanned with that database.
