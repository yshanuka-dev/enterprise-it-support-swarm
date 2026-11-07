# Redis Out of Memory (OOM)

**Runbook ID:** RB-REDIS-OOM
**Ticket type:** critical_outage
**Services:** redis, prod-cache-01
**Requires:** DevOps clearance for the restart step

## Symptoms
- Cache writes are rejected and `rejected_connections` keeps increasing.
- Applications time out or error on cache calls.
- Cache hit rate drops sharply and database CPU climbs because every miss falls through to the database.

## Diagnosis
1. Run `redis-cli INFO memory` and compare `used_memory` with `maxmemory`; note `maxmemory_policy`.
2. Typical root cause: the policy is `noeviction` (often changed during a maintenance window), so Redis rejects requests when full instead of evicting old keys. The expected policy for this cache is `allkeys-lru`.
3. Confirm with `redis-cli INFO stats | grep rejected_connections`, which should be increasing.

## Remediation
1. `redis-cli CONFIG SET maxmemory-policy allkeys-lru`
2. `redis-cli CONFIG SET maxmemory <limit>`; leave headroom below the machine memory (a past fix lowered an 8 GB limit to 6 GB).
3. Restart the instance: `redis-cli SHUTDOWN NOSAVE`, then start the service again. NOSAVE discards unsaved data, so only do this for a pure cache.
4. Fix the setting in the configuration repository so the change does not revert.

## Verification
- `redis-cli PING` returns PONG.
- `rejected_connections` stops increasing.
- Allow about 90 seconds for Redis and about 2 more minutes for application connection pools to reconnect.

## Escalation
This is a critical outage: escalate by voice to the on-call DevOps engineer and verify clearance before any restart. Stop and escalate if the instance holds data that cannot be rebuilt or the problem returns after the fix.
