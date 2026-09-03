# TrainingPeaks activation checklist

TrainingPeaks currently grants API access only to approved commercial partners. Glycofy keeps its existing CSV import available until credentials and partner documentation are issued.

When access is approved:

1. Record the assigned authorization, token, API base URLs, scopes, rate limits, and webhook requirements from TrainingPeaks.
2. Configure `TRAININGPEAKS_CLIENT_ID`, `TRAININGPEAKS_CLIENT_SECRET`, and `TRAININGPEAKS_REDIRECT_URI` in Render.
3. Implement OAuth using the existing encrypted `oauth_accounts` token storage; never store tokens in browser storage or logs.
4. Import completed workouts and the supported seven-day planned-workout window into the provider-neutral activity tables.
5. Add idempotent cursor-based sync, token refresh, disconnect/data deletion, audit events, and contract tests against the partner sandbox.
6. Complete a privacy review, load/rate-limit testing, and TrainingPeaks launch approval before exposing the Connect button.

Until those assigned API details exist, do not guess endpoint paths or show a nonfunctional connection control.
