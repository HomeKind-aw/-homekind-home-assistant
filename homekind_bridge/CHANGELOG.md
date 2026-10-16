# Changelog

## 0.6.0 — 16 October 2026

- Include media players in discovery and selected readings: TVs, speakers, receivers and projectors.
- Share standard playback states for simple HomeKind room readings and activity summaries.
- Keep titles, artists, artwork and media URLs out of shared readings.
- Playback remains read-only and does not add a notification for every state change.
- Retain pairing, sensor selections, existing light controls and fast live updates.

## 0.5.0 — 16 October 2026

- Handle light commands independently from sensor uploads; up to three leased commands run together.
- Wake uploads on readings and coalesce bursts to at most one upload per second.
- Save each heartbeat as a batch, avoiding a disk flush for every cached device.
- Stop pending service calls when settings change or connector access ends.
- Retain pairing, device selections, read-only defaults and confirmation of actual device states.

## 0.4.0 — 16 October 2026

- Add an explicit local switch for light and plug controls; read-only remains the default.
- Keep current pairing and selected devices when changing control mode.
- Recommend useful readings, add bulk selection, and keep other supported settings optional.
- Include Home Assistant entity categories for HomeKind display defaults.


## 0.3.1 — 15 October 2026

- Restore the Supervisor environment when starting under s6 so the setup screen can open on Home Assistant Green.
- Verify the actual container entrypoint on ARM and amd64, including unauthorised Ingress rejection.

## 0.3.0 — 15 October 2026

- Home Assistant repository layout for installation from the App store.
- Branded, phone-friendly Ingress setup with sensor checkboxes and connection status.
- Setup starts without a pairing code, and preserves existing app credentials.
- Enforced read-only phone setup, bounded requests, CSRF checks and Supervisor-only Ingress access.
- Save an empty selection to pause; sensor selection and credentials remain local.
- Temporary outages retry automatically; refused pairing waits for a fresh code.

## 0.2.0 — 14 October 2026

- Explicit entity allowlist, live WebSocket readings, snapshots and bounded outage recovery.
- Read-only default and observation freshness.
