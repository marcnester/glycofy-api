# Dependency review — November 23, 2026

| PR | Scope | Review outcome |
| --- | --- | --- |
| #114 | 20 Python runtime/development updates | Merged after updated-base CI and combined staging validation. Removed retired SQLAlchemy mypy plugin; native Mapped annotations pass mypy. Ruff, all 747 tests, pip-audit, PostgreSQL and container checks pass. |
| #116 | Nine GitHub Actions updates, superseding #112 | Merged after updated-base CI. SHA-pinned actions retain the shared fresh-database security gate. Artifact upload/download and checksum-verified publication passed in the combined workflow. No permissions expansion. |
| #67 | Four Node dependency updates | Merged after updated-base CI and combined staging validation. UI tests and npm audit pass. Removed dotenv preload/vault interfaces are unused. Node modules and a Node server do not enter the production Python image. |
| #93 | Python 3.11 to 3.15 base image | Hold. Container install fails because pinned psycopg-binary 3.3.5 has no Python 3.15 wheel. The rootfs collector also hard-codes Python 3.11 and CI tests Python 3.11. Requires a coordinated interpreter, collector and native-extension migration, not a tag-only update. |

GitHub Dependabot currently reports no open security alerts. That result does not cover operating-system packages, hosted infrastructure or unfixed vulnerabilities in the complete container inventory.

The urgent OpenSSL change and scanner-database consistency work shipped separately in PR #115 and remain present in this dependency release.

## Validated release — November 25, 2026

PRs #114, #116 and #67 are merged after all required checks passed on each updated base. Main at `89a48504df4126d1075f85673abdc312d9365c39` has the exact same Git tree as combined staging candidate `b29c83aebdd1d1e056476d7e482f81025ef239e6`. PR #116 supersedes #112.

The release promotes the exact staging image `sha256:1eb26a9724c5316e6027612daf88b4c4dcc553c23bb87388991576b7aae1e7ec`; no rebuild or model/prompt changes are included. Combined CI and the artifact publication workflow passed. Both scanners used one fresh shared vulnerability database; blocking results contain no unresolved high/critical findings or secrets. The two raw OpenSSL findings are covered by verified upstream backports.

Real model generation on isolated staging completed a vegan, wheat-free 35-meal week in 104.911 seconds. A second week completed in 120.287 seconds with 30 generated meals and five existing locked meals preserved exactly. Saved-meal quality, daily nutrition targets, verified nutrient sources, portions, variety, and cooking-direction checks passed. A direct save to the locked day was rejected. All pre-existing staging plans and preferences were unchanged, and temporary fixture preferences/lock states were restored.

Staging runtime checks verified PostgreSQL TLS, schema head `add_grocery_stock`, readiness, encryption round trips, verified outbound HTTPS to OpenAI and Strava, and both OpenSSL fixes. There is no new migration in this release. Anonymized test evidence is in [dependency-release-2026-11-25.json](evaluations/dependency-release-2026-11-25.json).

Production promotion must verify readiness and runtime dependencies, then compare every original saved plan/meal/ingredient and preference against its pre-deploy fingerprint. Temporarily protected unlocked plans must have their original lock states restored after startup workout sync; existing locked plans must remain locked throughout.
