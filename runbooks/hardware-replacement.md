# Hardware Replacement and Provisioning

**Runbook ID:** RB-HARDWARE
**Ticket type:** asset_provisioning
**Services:** assets_inventory, hardware workflow
**Requires:** Look up the device in assets_inventory before approving anything

## Case: laptop LCD panel failure (spreading dead pixels)
**Symptoms:** Dead pixels spreading across the screen over days.
**Fix:** Confirm the model in assets_inventory, create a hardware ticket for a panel replacement, check that a compatible panel is in stock, and book a technician (repair takes about 45 minutes). Offer a temporary external monitor if the user needs one today.
**Verify:** The panel is replaced and the ticket is closed.

## Case: docking station USB-C failure
**Symptoms:** USB-C port on a docking station stops working.
**Cause:** A known issue on CalDigit docks with firmware below 1.4.0.
**Fix:** Check the dock's asset record for its firmware version. Offer to flash the update (about 15 minutes) or replace the dock with a new unit that has current firmware.

## Case: monitor request
**Fix:** Approve the standard monitor for the role (a 27-inch 4K USB-C Dell U2723QE for design roles), submit the purchase order (ships in about 2 business days), and return the old monitor to the inventory pool.

## Case: laptop refresh (old or slow device)
**Fix:** Compare the device age with the 3-year refresh policy. A device well past it is auto-approved: submit the refresh request, deliver the new laptop within about 5 business days, and schedule a data migration session.

## Case: keyboard replacement
**Fix:** Allocate a replacement from inventory and let the user choose pickup at the IT desk or delivery to their desk.

## Escalation
If stock is unavailable or the request is outside policy, escalate to the asset manager.
