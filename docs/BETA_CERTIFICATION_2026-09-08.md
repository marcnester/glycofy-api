# Glycofy beta certification — September 8, 2026

## Release candidate

- Production commit: `ed34496`
- Environment: `https://app.glycofy.ai`
- Certification status: **In progress**

## Passed gates

- Clean Git working tree at the production commit.
- 122 Python tests passed after the first certification correction.
- Ruff passed.
- GitHub Security and tests workflow passed.
- GitHub CodeQL workflow passed.
- npm production audit: 0 known vulnerabilities.
- Python production requirements audit: 0 known vulnerabilities.
- Production health endpoint returned `status: ok`.
- Production login response includes CSP, HSTS, clickjacking protection, MIME-sniffing protection, a restrictive permissions policy, same-origin opener/resource policies, and a request ID.
- An untrusted `Origin` received no `Access-Control-Allow-Origin` permission.
- Unauthenticated requests to account, export, operations, analytics, and training endpoints were rejected.
- Cloudflare reported the login response as dynamic rather than publicly cached.
- Disposable email/password signup succeeded and sent a verification message.
- New-athlete onboarding correctly began at 17% and reached 100% after required athlete fields were saved.
- Empty training showed the standard-target warning; adding a 75-minute hard HYROX key session changed the status to partial context with one upcoming workout.
- Today AI completed with personalized meals, ingredient quantities, and cooking guidance.

## Certification corrections

- Today AI incorrectly reused the durable weekly-job heading and told users they could leave the page. The candidate now labels this state “Creating today's plan” and asks users to keep the page open; weekly planning retains the safe-to-leave message.
- Password-reset links redirected an already signed-in recipient to Home before showing the reset form. Reset mode now takes precedence over an existing session when a token is present.
- UI HTML relied on heuristic browser caching, which could briefly preserve an older account-flow script after deployment. HTML documents now explicitly use `Cache-Control: no-store`; versioned static assets remain cacheable.

## Pending gates

- Disposable-account email signup, verification, password recovery, data export, and deletion.
- End-to-end Google signup using the disposable account.
- Authenticated Today, Weekly, Training, Profile, Grocery, feedback, and operations walkthrough on the final candidate.
- Exact iPhone Safari and Android Chrome viewport/device matrix.
- Keyboard, screen reader, zoom, reduced-motion, slow/offline, and expired-session walkthroughs.
- Render database restore drill and recovery-time evidence.
- Qualified legal review of the Privacy Policy and Terms launch drafts.

## Safety constraints

- Never delete or disconnect the owner account during certification.
- Use a disposable account for destructive lifecycle testing.
- Obtain confirmation before sending external test emails or creating potentially billable restore infrastructure.
