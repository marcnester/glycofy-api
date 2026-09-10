# Backup restore drill — 2026-09-09

Status: **PASS**

## Scope

- Source: production Render PostgreSQL `glycofy-db`
- Recovery method: Render point-in-time recovery into an isolated database
- Selected recovery point: 2026-09-09 21:49:51 PDT
- Restore target: `glycofy-restore-drill-20260909`
- Production application/database changes: none
- Direct public access to both databases: blocked

## Recovery objectives observed

- Available recovery window: three days on the current workspace tier
- Approximate recovery-point lag when the drill began: eight minutes
- Restore target became available in approximately three minutes
- Read-only application-level verification completed immediately after the
  database became available

These observations are evidence from one small-database drill, not guaranteed
service-level objectives. For the controlled beta, use a provisional RPO of 24
hours and RTO of 60 minutes until repeated drills establish a reliable bound.

## Verification

The production web service connected to the restored database over Render's
private network using the read-only `scripts/verify_backup_restore.py` checks.
No credentials were printed or copied outside Render.

- Connectivity: PASS
- Required tables: PASS
- Alembic revision: `add_snack_preferences` (current head), PASS
- Representative weekly-job read: PASS
- Restored-versus-live representative row counts: exact match

| Table | Restored | Live |
| --- | ---: | ---: |
| `users` | 1 | 1 |
| `plans` | 13 | 13 |
| `plan_meals` | 53 | 53 |
| `weekly_planning_jobs` | 2 | 2 |
| `ai_operation_metrics` | 44 | 44 |
| `beta_feedback` | 1 | 1 |
| `product_events` | 89 | 89 |

An independent logical export was requested at approximately 21:58 PDT and
completed at 22:01 PDT as a `.dir.tar.gz` archive. Render retains completed
export files for at least seven days.

## Cleanup

The isolated restore target was deleted after verification to stop billing. The
logical export remains under the production database's Recovery page according
to Render's normal retention policy.

## Follow-up cadence

- Repeat this drill monthly during beta and after material database changes.
- Record recovery point, observed lag, restore duration, migration head, and
  workflow or console evidence each time.
- Upgrade the workspace recovery window if three days is insufficient for the
  product's formal RPO before general availability.
