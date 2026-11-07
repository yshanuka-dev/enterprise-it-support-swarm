# VPN Will Not Connect (stale session)

**Runbook ID:** RB-VPN-SESSION
**Ticket type:** access_identity
**Services:** VPN, RADIUS

## Symptoms
- The VPN refuses to connect, often right after a previous connection dropped.
- Repeated attempts fail even though credentials are correct.

## Diagnosis
1. Check the RADIUS accounting records for a phantom session for this user. The previous tunnel was not torn down properly, which blocks new connections.
2. Check that the client certificate is still valid: `openssl x509 -in /etc/openvpn/client.crt -checkend 0`.

## Remediation
1. Ask the user to disconnect the VPN client completely and wait about 10 seconds.
2. Clear the stale session on the RADIUS server: `radclient -x disconnect $RADIUS_SERVER:3799 disconnect 'Acct-Session-Id=<session_id>'`.
3. Reset the client certificate if it is invalid or expired.
4. Ask the user to reconnect.

## Verification
- The user reconnects successfully on the next attempt.

## Escalation
If the certificate cannot be reset or the failure repeats for the same user, escalate to L2 network support.
