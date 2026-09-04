# Production operations

## Weekly plan recovery

Weekly AI jobs are durable in PostgreSQL. At every application startup, Glycofy finds jobs left `queued` or `running`, honors requested cancellations, requeues interrupted jobs below `WEEKLY_JOB_MAX_ATTEMPTS`, and atomically claims each job in the current process. Exhausted jobs receive a privacy-safe error reference while the correlated stack trace remains in structured logs.

Terminal jobs are retained for `WEEKLY_JOB_RETENTION_DAYS` (30 by default). Anonymous AI metrics are retained for `AI_METRIC_RETENTION_DAYS` (90 by default).

## Monitoring

Set `ADMIN_EMAILS` to a comma-separated operator allowlist. An authenticated operator can open `/ui/operations.html` or request `GET /v1/operations/ai-summary?hours=168` for success/failure rates, p50/p95 provider latency, token totals, estimated cost, active/failed weekly jobs, and operation breakdowns.

The endpoint never returns prompts, meals, health fields, IP addresses, or user identifiers. Search Render logs for a failed job's `error_reference` to find its JSON stack trace.

Recommended beta alerts are failure rate above 5%, p95 weekly latency above 120 seconds, any exhausted recovery, or sustained readiness failure.

## Backup restore drill

A backup is not proven until restored. At least monthly:

1. Restore a Render PostgreSQL recovery point into an isolated temporary database.
2. Set the GitHub Actions secret `RESTORE_DATABASE_URL` to that database.
3. Manually run **Backup restore verification**.
4. Retain the workflow link and recovery-point timestamp in the operations log.
5. Delete the temporary database and secret.

The verifier is read-only. It checks connectivity, required tables, the Alembic head, representative reads, and table counts. Never point it at production merely to avoid a real restore.

## Scaling boundary

Render intentionally runs one web process today. Background execution and rate limits remain process-local. `WEB_PROCESS_COUNT` defaults to `1`; production rejects a higher value unless `SHARED_JOB_QUEUE_URL` and `SHARED_RATE_LIMIT_URL` are configured.

Before adding web processes or instances, move weekly execution to a dedicated shared-queue worker, move rate limiting to a shared store, add worker heartbeats/leases, and run concurrency/failover tests. This is not required for a controlled beta on one Starter web instance.
