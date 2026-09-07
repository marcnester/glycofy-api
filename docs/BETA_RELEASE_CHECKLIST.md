# Glycofy beta release checklist

Use this checklist before inviting a new beta cohort and after changes to authentication, onboarding, planning, training, or grocery workflows.

## Automated release gate

- Run `ruff check .`.
- Run `pytest -q` and require every test to pass.
- Run `npm test` and resolve high-severity dependency findings.
- Apply the full Alembic chain to a fresh PostgreSQL database.
- Confirm the production `/health` endpoint returns `status: ok` after deployment.

## New-athlete journey

- Create an account with email/password and with Google.
- Complete the athlete profile, diet, allergy, exclusion, goal, timezone, and unit fields.
- Confirm incomplete onboarding explains what is missing without blocking exploration.
- Add an upcoming workout manually and import a representative TrainingPeaks CSV.
- Connect and disconnect Strava; verify the user understands which data is imported.
- Generate Today and Weekly AI plans with full, partial, and no training context.
- Confirm slow generation shows progress and interrupted jobs recover after a deployment.
- Review recipes, timings, nutrition, exclusions, swaps, meal feedback, and questionable-output fallbacks.
- Normalize and approve the grocery list, adjust servings, mark pantry items, and export CSV/TXT.
- Download account data, request a password reset, and verify the account-deletion confirmation flow.

## Cross-device matrix

- iPhone Safari: 390 × 844 and 320 × 568 viewport checks.
- Android Chrome: 360 × 800 and 412 × 915 viewport checks.
- Desktop Safari and Chrome: 1280 × 800 and 1440 × 900 viewport checks.
- On every page, verify no document-level horizontal scrolling, clipped dialogs, hidden actions, or keyboard-obscured fields.
- Test portrait and landscape orientation for Plan, Training, Profile, and Grocery.

## Accessibility and resilience

- Navigate the entire primary journey using only the keyboard.
- Confirm visible focus, logical focus order, usable skip link, dialog focus containment, and Escape behavior.
- Check headings, landmarks, labels, status announcements, contrast, 200% zoom, and reduced motion.
- Exercise empty, loading, slow, validation-error, server-error, expired-session, offline, and reconnection states.
- Confirm every destructive action requires clear confirmation and explains recovery implications.

## Privacy and operations

- Confirm analytics and feedback contain page, coarse browser/viewport, and request IDs only—never meals, macros, health fields, workouts, query strings, or full user agents.
- Review failed jobs and beta feedback using privacy-safe operations views.
- Verify database backup restoration on the scheduled cadence.
- Confirm alert email, AI latency/failure/cost telemetry, rate limits, job cleanup, and feature flags are healthy.
- Record the deployed commit, test results, known limitations, and rollback point in the release notes.
