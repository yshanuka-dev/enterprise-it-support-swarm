# Auth Service Down (pods OOMKilled)

**Runbook ID:** RB-AUTH-OOM
**Ticket type:** critical_outage
**Services:** auth-api, auth service
**Requires:** DevOps clearance for scaling and resource changes

## Symptoms
- Authentication requests fail (502 or timeouts) and users cannot log in.
- Zero healthy auth-api replicas are running.
- Pods show `OOMKilled`.

## Diagnosis
1. `kubectl get pods` and `kubectl describe pod <pod>`: look for `OOMKilled` and the memory limit.
2. Compare the limit (512 MB in the past incident) with real usage (peaked near 1.2 GB before the kill).
3. Typical root cause: the service loads the entire user permission matrix into memory at startup; user growth (50K to 75K users) pushed it past the limit.

## Remediation
1. `kubectl set resources deployment auth-api -c auth --limits=memory=2Gi`
2. `kubectl scale deployment auth-api --replicas=4`
3. Pods should become healthy within about 45 seconds.
4. File an engineering ticket for the permanent fix: lazy-load permissions or use a Redis-backed permission cache.

## Verification
- `kubectl get pods` shows all replicas Running and Ready.
- Login succeeds end to end and the 502 rate returns to normal.

## Escalation
Critical outage: escalate by voice to on-call DevOps and verify clearance before changing resources. Escalate again if pods are killed repeatedly after the limit increase.
