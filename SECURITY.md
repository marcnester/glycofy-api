# Glycofy security baseline

Glycofy handles health, dietary, allergy, and connected-account data. Security issues should be reported privately to the project owner rather than opened as public issues.

## Production requirements

- Set `ENV=production` and `DEBUG=false`.
- Use a managed PostgreSQL database with encrypted, tested backups.
- Set a random `JWT_SECRET` of at least 32 characters.
- Generate a separate Fernet key for `OAUTH_TOKEN_ENCRYPTION_KEY`.
- Set `COOKIE_SECURE=true`, an HTTPS `PUBLIC_BASE_URL`, and exact `ALLOWED_ORIGINS` and `ALLOWED_HOSTS` values.
- Keep `ENABLE_DEV_ROUTES=false`.
- Run `alembic upgrade head` before starting the new application version.
- Encrypt legacy provider credentials with `python -m scripts.encrypt_oauth_tokens` after backing up the database.
- Put the application behind a trusted TLS reverse proxy. Do not accept forwarded client-IP headers from the public internet.
- Replace the in-process rate limiter and background work with shared Redis-backed services before horizontal scaling.

Generate an OAuth encryption key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Verification gates

Pull requests run unit/security regression tests, Ruff, Bandit, pip-audit, npm audit, and Gitleaks. Production releases must additionally receive authenticated two-user authorization testing, dynamic application testing in staging, restore testing, and an independent penetration test.

The control baseline is OWASP ASVS 5.0 plus OWASP API Security Top 10 2023 and OWASP AISVS 1.0. The Top 10 lists are awareness documents, not a substitute for testable ASVS requirements.

## Operational work required before launch

- Centralized redacted logs, immutable audit events, metrics, tracing, alerting, and uptime checks
- Incident response, breach notification, credential rotation, and OAuth revocation runbooks
- Privacy policy, terms, consent records, retention schedule, user export, and account deletion
- Vendor and data-flow review for OpenAI, Google, Strava, hosting, email, and future TrainingPeaks access
- A managed job queue with idempotency, cancellation, per-user quotas, and progress reporting for AI weekly planning

## Logging and alerting starter policy

Application and access logs are emitted as one-line JSON to standard output. Every completed request includes `request_id`, `method`, `path`, `status_code`, and `duration_ms`; responses return the same ID in `X-Request-ID`. The application accepts a caller-provided ID only when it uses a constrained safe format.

Security-relevant authentication and OAuth activity is also stored in `security_audit_events`. Client addresses are HMAC-hashed and truncated; emails, passwords, session tokens, OAuth credentials, and provider response bodies must never be placed in logs or audit metadata. Run `python -m scripts.purge_security_audit_events` on a schedule matching `SECURITY_AUDIT_RETENTION_DAYS`. Use `python -m scripts.security_audit_summary` for a privacy-safe 24-hour summary.

Configure the production log platform to page on logger `glycofy.security.alert` and create initial rules for:

- Any `unhandled_request_exception`
- Any `oauth_google_callback` or `oauth_strava_callback` with `invalid_state`
- Any `authentication_rate_limited` event
- Five or more failed logins for one `client_id_hash` in 15 minutes
- A sudden increase in HTTP 401, 403, 413, 429, or 5xx responses
- Missing application heartbeat or absence of logs from an expected instance

Warnings such as individual failed logins and CSRF rejections should create searchable security signals but should only page after aggregation thresholds are crossed. Alert destinations and escalation policies belong in the deployment platform; application code should not contain Slack, email, or PagerDuty credentials.

### Temporary email delivery

Alert-class events can temporarily be delivered to `marcnester@gmail.com` through SMTP. Delivery is non-blocking, uses a bounded queue, and suppresses duplicate event/outcome combinations for 15 minutes by default. Set these only in the deployment secret store or local `.env`, never in source control:

```dotenv
SECURITY_ALERT_EMAIL_ENABLED=true
SECURITY_ALERT_EMAIL_TO=marcnester@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-sending-account@example.com
SMTP_PASSWORD=provider-app-password-or-SMTP-secret
SMTP_FROM_EMAIL=your-sending-account@example.com
SMTP_USE_TLS=true
```

For Gmail, use an application-specific credential or a transactional-email provider; do not store a normal mailbox password. Alert email is a temporary delivery channel. The structured log platform should remain the authoritative alert source because an application or SMTP outage can prevent the application from sending its own alert.
