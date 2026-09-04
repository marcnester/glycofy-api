# Deploying Glycofy on Render

See [Production operations](PRODUCTION_OPERATIONS.md) for job recovery, monitoring, retention, backup drills, and the single-instance scaling boundary.

Glycofy is defined as a Render Blueprint in `render.yaml`. The initial
production topology is deliberately small: one Docker web service and one
private managed PostgreSQL database. Alembic migrations run as a pre-deploy
command before each release receives traffic.

## Before creating the Blueprint

Push the deployment files to the repository's default branch and have these
two secrets ready. Do not commit either value.

```bash
openssl rand -hex 32
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Use the first value for `JWT_SECRET` and the second for
`OAUTH_TOKEN_ENCRYPTION_KEY`.

## Create the services

1. In Render, choose **New > Blueprint** and connect the Glycofy GitHub
   repository.
2. Render detects `render.yaml`. Review the web service and database costs.
3. Enter the two required secret values when prompted. `OPENAI_API_KEY` is
   optional for the initial deployment, but AI meal generation requires it.
4. Apply the Blueprint and wait for both the pre-deploy migration and web
   service deploy to succeed.
5. Verify `https://glycofy-api.onrender.com/health`, then `/ready`, then
   `/ui/login.html`.

If Render assigns a different `onrender.com` hostname, add that exact HTTPS
origin to `ALLOWED_ORIGINS`. The host is already accepted by the restricted
`*.onrender.com` host pattern.

## Connect the production domain

Add `app.glycofy.ai` as the web service's custom domain. Render will provide
the DNS target to add in Cloudflare. Keep the existing `glycofy.ai` coming-soon
site in place until the application has passed the smoke checks.

The Blueprint already uses `https://app.glycofy.ai` for the public URL,
allowed origin, and JWT issuer. Configure integrations with these callbacks:

- Google: `https://app.glycofy.ai/oauth/google/callback`
- Strava: `https://app.glycofy.ai/oauth/strava/callback`

Then add their client IDs and secrets in the Render service environment.

## Email security alerts

Alert email is disabled for the first deploy so missing SMTP credentials do
not make the application fail closed at startup. After obtaining a Glycofy
mailbox or SMTP provider, set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`,
`SMTP_PASSWORD`, and `SMTP_FROM_EMAIL`, then set
`SECURITY_ALERT_EMAIL_ENABLED=true`.

## Release and rollback checks

For each production deploy:

1. CI must pass before Render deploys (`autoDeployTrigger: checksPass`).
2. `alembic upgrade head` must succeed before the new release starts.
3. `/health` confirms the process is alive; Render uses `/ready` to keep a
   release out of rotation until its database is reachable.
4. Check login, profile loading, daily planning, and one AI plan operation.

Do not add multiple web instances until the in-memory authentication rate
limiter is moved to Redis. Do not add a background worker until queued work is
implemented as a durable job rather than an in-process task.
