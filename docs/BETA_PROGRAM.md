# Glycofy beta program

## Feedback experience

Every authenticated primary page loads the shared **Send feedback** control. A tester can classify feedback, optionally rate the experience, and write up to 1,200 characters. Glycofy automatically attaches only:

- the UI path without query parameters;
- coarse browser family;
- mobile, tablet, or desktop viewport;
- the feedback submission request ID; and
- the most recent related request ID when available.

Meals, macros, health profile fields, workouts, full user-agent strings, IP addresses, and URL query parameters are never attached. Feedback is private to Glycofy operators and included in the user's data export.

## Beta analytics

Analytics is first-party and authenticated. Only these allowlisted events are accepted: page view, onboarding completed, weekly plan started/completed, grocery opened/approved/handoff started, request failed, and feedback opened/sent. Events contain no free-form properties. Browser session identifiers are HMAC-hashed before storage.

Operators configured through `ADMIN_EMAILS` can use `/ui/operations.html` to see active-tester counts, core funnel events, the feedback queue, and sanitized failed jobs. The API never returns user identifiers with these views.

## Feature flags and retention

`FEATURE_FLAGS` is a comma-separated list. `beta_feedback` and `beta_analytics` ship enabled, and each also has an emergency boolean kill switch. Product events expire after `PRODUCT_EVENT_RETENTION_DAYS` (180 by default); feedback expires after `BETA_FEEDBACK_RETENTION_DAYS` (365 by default) or immediately when the user deletes their account.

Experimental features should be added as new explicit flag names and enforced server-side—not merely hidden in the browser.
