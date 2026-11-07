# Account and Access Problems

**Runbook ID:** RB-ACCOUNT-ACCESS
**Ticket type:** access_identity
**Services:** Active Directory, SSO, MFA, staging database, production servers
**Requires:** SQL clearance check before any permission change

## Case: new password not accepted (stale Kerberos tickets)
**Symptoms:** After an AD password change the user is rejected by Jenkins, Grafana, the wiki or SSO.
**Cause:** Old Kerberos tickets signed with the previous password hash conflict with the new password.
**Fix:** Run `kdestroy -A`, then `kinit <employee_id>@<REALM>`, then `klist` to confirm valid tickets. Reset the account lockout counter and force a password sync across the integrated services. `net ads testjoin` verifies domain trust.
**Verify:** The user logs in to the affected services with the new password.

## Case: MFA codes rejected (TOTP clock drift)
**Symptoms:** The user is locked out because authenticator codes are rejected.
**Cause:** A server-side clock drift on the TOTP validation server (47 seconds ahead in a past incident) desynchronised tokens.
**Fix:** Correct the server time, regenerate the user's TOTP seed, send a new QR code to the registered email, and invalidate old codes.
**Verify:** The user scans the new QR code and a fresh code is accepted.

## Case: cannot connect to the staging database (deny list)
**Symptoms:** Connection to the staging database is refused for one engineer.
**Cause:** An automated security scan added the account to the `pg_hba.conf` deny list because it showed no recent activity.
**Fix:** Verify clearance first. Remove the entry from `/etc/postgresql/14/main/pg_hba.conf`, run `SELECT pg_reload_conf()`, and add the user with `GRANT staging_readwrite TO "<employee_id>"`.
**Verify:** `psql -h staging-db.internal -U <employee_id> -d staging -c 'SELECT 1'` succeeds.

## Case: production SSH and deploy access for a new engineer
**Symptoms:** A new DevOps or engineering hire cannot reach production servers or the deployment pipeline.
**Fix:** Verify clearance first. Add the user's SSH public key to the production servers, add the user to the `deploy` group in the identity provider, and update sudoers for deployment tasks.
**Verify:** The user can SSH to the servers and run a deployment.

## Escalation
If clearance is insufficient, do not apply the change: escalate to a DevOps lead for approval. Escalate to L2 if the same user is blocked again after the fix.
