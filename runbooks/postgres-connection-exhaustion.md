# Postgres Connection Exhaustion (database unresponsive)

**Runbook ID:** RB-PG-CONNECTIONS
**Ticket type:** critical_outage
**Services:** postgres-primary, checkout-service
**Requires:** DevOps clearance for terminating connections and rollbacks

## Symptoms
- The database stops accepting connections; thousands of clients are waiting.
- Services report connection timeouts or "too many clients" errors.
- Many sessions are shown as `idle in transaction`.

## Diagnosis
1. `SELECT count(*) FROM pg_stat_activity` and compare with `max_connections` (100 in past incidents).
2. `SELECT count(*) FROM pg_stat_activity WHERE state='idle in transaction'`; a very high number (for example 87 of 100) points to leaked connections.
3. Find the cause in recent deployments. A past incident was a retry loop in a new checkout-service release that opened a new connection per retry without closing it.

## Remediation
1. Terminate the leaked sessions: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle in transaction' AND query_start < now() - interval '5 min'`
2. Roll back the offending release: `kubectl rollout undo deployment/checkout-service`
3. Optional, only with DBA approval: promote the read replica as a temporary primary while connection state is cleaned up.

## Verification
- `SELECT count(*) FROM pg_stat_activity WHERE state='active'` drops below about 20.
- The database accepts new connections within a few minutes.
- Watch `pg_stat_activity` and the connection pool metrics in Grafana.

## Escalation
Critical outage: escalate by voice to on-call DevOps, verify clearance first, and open an engineering ticket for the leak in the rolled-back release.
