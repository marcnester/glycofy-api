# Glycofy beta certification — September 8, 2026

## Release candidate

- Production commit: `ed34496`
- Environment: `https://app.glycofy.ai`
- Certification status: **In progress**

## Passed gates

- Clean Git working tree at the production commit.
- 121 Python tests passed.
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
