# Slow API from a Missing Database Index

**Runbook ID:** RB-SLOW-API-INDEX
**Ticket type:** service_degradation
**Services:** checkout API, orders database

## Symptoms
- The checkout API (or similar) is slow for users; latency rises and database CPU climbs.
- Timeouts appear on queries that fetch a user's recent records.

## Diagnosis
1. Check recent latency and database CPU metrics for the affected service.
2. Run `EXPLAIN` on the slow query. In a past incident `SELECT * FROM orders WHERE user_id = $1 ORDER BY created_at DESC LIMIT 10` showed a `Seq Scan` over about 12 million rows.
3. A sequential scan on a large table where an index on the filter and sort columns is missing is the root cause.

## Remediation
1. `CREATE INDEX CONCURRENTLY idx_orders_user_created ON orders(user_id, created_at DESC)`
2. This builds without blocking writes and took about 3 minutes in the past incident. `CONCURRENTLY` cannot run inside a transaction block.

## Verification
- `EXPLAIN` now shows an `Index Scan`.
- P95 latency is back under the 200 ms target and database CPU falls.

## Escalation
If latency does not improve after the index exists, escalate to L2 to look for other slow queries or lock contention.
