# Glycofy beta release evidence — September 7, 2026

## Candidate

- Branch: `main`
- Candidate commit: `3f9dcc3`
- Last known-good rollback point before this acceptance pass: `42e2162`
- Production: `https://app.glycofy.ai`

## Automated gates

- Python: 121 tests passed.
- Lint: Ruff passed.
- JavaScript dependencies: `npm audit --omit=dev --audit-level=high` reported no vulnerabilities.
- Python dependencies: `pip-audit` reported no known vulnerabilities.
- PostgreSQL: the complete Alembic chain applied successfully to a fresh PostgreSQL 16 UTF-8 database and reached `add_beta_feedback_analytics (head)`.
- Migration compatibility: nullable `user_preferences.diet_type` and `user_preferences.ingredient_exclusions` were present after the full chain.
- GitHub: Security and tests plus CodeQL passed for `4564235` and `35f864c`; final candidate checks must be green before invitation.

## Production acceptance evidence

- Today AI completed in 26 seconds and retained ingredient quantities after reload.
- Weekly AI completed in 53 seconds with visible progress and durable job state.
- Profile is complete; diet, allergy, exclusion, units, goal, and training context render correctly.
- Manual training entry, TrainingPeaks CSV import, supported sports, recipe instructions/timings, grocery normalization/approval, and CSV/TXT export were exercised.
- Account data export works. Account deletion presents an explicit irreversible-data warning, requires typing `DELETE`, and keeps the permanent action disabled beforehand. The real owner account was not deleted.
- Strava reports connected. The real owner connection was not disconnected during acceptance testing.
- The weekly review page now renders available days when one or more dates have no plan.
- The operator dashboard is restricted by `ADMIN_EMAILS` and exposes aggregate telemetry without prompts, meals, health fields, IP addresses, or user identifiers.
- Seven-day production telemetry at verification: 14 requests, 0.0% failures, 45.3-second p95 latency, 37,254 tokens, and $0.030 estimated cost; no failed weekly jobs.
- Cloudflare free managed protections are active. Authentication POSTs are limited to 10 requests per 10 seconds per IP, with a 10-second block.
- Render PostgreSQL has point-in-time recovery enabled for three days and blocks direct public database traffic.

## Remaining human/external gates

- Run the exact physical-device matrix in `BETA_RELEASE_CHECKLIST.md`. The controlled in-app browser clamps its minimum viewport to 736px, so it cannot certify the 320–412px Safari/Chrome targets. Static responsive tests and the previously reported iPhone defect are covered, but physical Safari and Chrome sign-off is still required.
- Perform a temporary Render restore drill and record recovery time. This can create a billable database and must be explicitly approved before execution.
- Have qualified counsel review the Privacy Policy and Terms launch drafts, including operator identity, jurisdiction, age eligibility, retention, subprocessors, and health-data obligations.
- Use a disposable beta account for the final destructive deletion test and for end-to-end Google signup. Never use the owner account for destructive acceptance testing.

## Beta decision

The application is suitable for a small, invite-only beta after the final candidate CI/deploy checks pass, provided testers are told that the policies remain launch drafts and the three human/external gates above are tracked to closure before a broader public launch.
