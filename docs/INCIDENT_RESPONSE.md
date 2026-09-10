# Glycofy incident-response runbook

Owner: Glycofy operator
Review cadence: quarterly and after every material incident
Scope: production application, Cloudflare, Render, PostgreSQL, GitHub, email,
OAuth providers, AI provider, and grocery integrations

## Severity and response targets

| Severity | Examples | Acknowledge | Contain |
| --- | --- | ---: | ---: |
| SEV-1 | Confirmed data exposure, active account takeover, leaked production secret, destructive database activity | 15 min | 60 min |
| SEV-2 | Exploitable auth bypass, sustained attack, material service outage, repeated unauthorized access attempts | 30 min | 4 hr |
| SEV-3 | Isolated suspicious event, blocked exploit, single-user defect with no confirmed disclosure | 1 business day | 3 business days |

When uncertain, start one level higher. Never delay containment while trying to
produce a perfect diagnosis.

## First 15 minutes

1. Record UTC start time, reporter, symptoms, affected surface, and the current
   production commit. Start an incident log outside the affected system.
2. Preserve alert email, request IDs, relevant Render JSON logs, Cloudflare
   security events, GitHub audit/deployment history, and database timestamps.
   Do not copy meal, workout, health, token, or password data into chat or email.
3. Open `/ui/operations.html` and query
   `/v1/operations/security-summary?hours=24`. Correlate alert request IDs with
   Render logs. Review AI failures and failed jobs separately.
4. Decide severity and whether the event is ongoing. If it is ongoing, contain
   first and investigate second.

## Containment playbooks

### Suspected account compromise

- Revoke the user's sessions by incrementing `users.token_version`; require a
  password reset. Do not send or log the password hash.
- Revoke connected Google/Strava tokens when provider compromise is plausible.
- Review only privacy-safe security events and request IDs needed to determine
  scope. Contact the user through the verified account address.

### Leaked application secret

Rotate in this order, one credential at a time, verifying health after each:

1. exposed provider credential or webhook secret;
2. `OAUTH_TOKEN_ENCRYPTION_KEY` with an explicit token re-encryption or provider
   reconnect plan—never rotate it blindly;
3. `JWT_SECRET`, which immediately invalidates every session;
4. database credential, SMTP token, and deploy hook as applicable.

Update Render secret variables, redeploy, verify `/health` and `/ready`, and
revoke the old credential at its issuer. Never place secret values in the
incident log, GitHub issue, commit, screenshot, or email.

### Active web attack or denial of service

- Use Cloudflare security analytics to identify the route and attack class.
- Prefer a narrow WAF/rate rule for the affected route, method, or verified
  signature. Use broad challenges or maintenance mode only when necessary.
- Confirm the Render origin hostname remains disabled and the database remains
  closed to public ingress.
- Preserve rule IDs and timestamps, then retest legitimate login and planning.

### Suspected database exposure or destructive change

- Block the suspected access path and rotate the database credential.
- Preserve database and platform logs. Do not modify or delete suspected
  evidence before capturing timestamps and identifiers.
- Restore the most recent safe recovery point into an isolated database and run
  `scripts/verify_backup_restore.py`. Compare counts and migration head before
  any production restoration.
- A production restore is a separate, explicitly approved destructive action.

### Malicious or unsafe AI output

- Disable the affected feature with a feature flag or provider credential if
  user safety is at risk.
- Preserve prompt version, quality failure code, model, timestamps, and request
  ID—not the user's private prompt or health details.
- Use verified fallbacks while the regression is reproduced in the adversarial
  safety harness.

## Investigation and scoping

- Build a UTC timeline from Cloudflare, Render, application audit events,
  GitHub, PostgreSQL, and provider dashboards.
- Determine entry point, affected accounts/records, data accessed or changed,
  persistence, and whether secrets or OAuth tokens were exposed.
- Treat absence of logs as uncertainty, not proof that no access occurred.
- Preserve original evidence read-only and record every containment change.

## Recovery

1. Fix the root cause and add a regression test.
2. Run the complete security workflow, migration chain, and production smoke
   checks. Deploy a reviewed commit through the normal CI gate.
3. Restore features gradually. Confirm alerts, audit writes, login/logout,
   planning, exports, and backups.
4. Monitor closely for at least 24 hours after SEV-1/2 containment.

## Communication and legal escalation

- Keep communications factual: what happened, what data is known to be
  affected, what was done, what users should do, and when the next update will
  arrive. Do not speculate.
- Notify affected users and relevant providers promptly when action can reduce
  harm. Obtain qualified legal advice immediately for any suspected personal or
  health-data breach; applicable deadlines depend on users, jurisdictions, and
  the facts.
- Use `security@glycofy.ai` as the public reporting address and verify its
  forwarding regularly.

## Closure and follow-up

An incident closes only after containment is removed safely, monitoring is
normal, affected credentials are revoked, user/legal communications are
complete, and evidence is retained. Within five business days, document root
cause, impact, detection gap, timeline, corrective actions, owners, and due
dates. Convert every preventable failure into a test, alert, or runbook update.

## Quarterly drill

1. Trigger a non-sensitive test alert in a non-production test context and
   verify redaction, cooldown, and delivery.
2. Locate its request ID in structured logs and the security summary.
3. Rehearse a JWT-secret rotation plan without revealing or changing the value.
4. Verify Cloudflare origin lockout, Render health alerts, database isolation,
   backup restoration, and `security@glycofy.ai` delivery.
5. Record date, participants, results, gaps, and corrective commits.
