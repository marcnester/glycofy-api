# Dependency review — November 23, 2026

| PR | Scope | Review outcome |
| --- | --- | --- |
| #114 | 20 Python runtime/development updates | Removed the retired SQLAlchemy mypy plugin in commit c297d22; ORM models already use native Mapped annotations. Isolated updated environment passes Ruff, mypy, all 747 tests and pip-audit. Pushed the fix to the existing PR. All PR checks, including container and PostgreSQL validation, are green after the fix; updated-base checks and staging validation remain release prerequisites; these runtime updates are separate from the urgent OpenSSL image. |
| #112 | Nine GitHub Actions updates | Reviewed SHA-pinned setup-python/node, CodeQL, cache, SBOM and upload/download-artifact changes. No requested permissions expansion. Existing CI passes. Rebase against the shared fresh-database security workflow and rerun before merge, so updates retain the new security gate. |
| #67 | Four Node dependency updates | Existing UI tests and npm audit pass. dotenv 18 removes preload and vault support and changes log output; neither removed interface is used by the repository. No Node modules or Node server enter the production Python image. Rerun updated-base checks before merge. |
| #93 | Python 3.11 to 3.15 base image | Hold. Container install fails because pinned psycopg-binary 3.3.5 has no Python 3.15 wheel. The rootfs collector also hard-codes Python 3.11 and CI tests Python 3.11. Requires a coordinated interpreter, collector and native-extension migration, not a tag-only update. |

GitHub Dependabot currently reports no open security alerts. That result does not cover operating-system packages, hosted infrastructure or unfixed vulnerabilities in the complete container inventory.

The urgent OpenSSL change and scanner-database consistency work are isolated in PR #115. The deployed image remains unchanged until that exact new artifact passes native, PostgreSQL, staging and security validation.
