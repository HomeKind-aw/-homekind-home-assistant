# 0.18.1 — maintenance release candidate, 19 October 2026

- Version alignment for the existing 0.18.0 grouped-light connector. No new device features or physical behaviour changes are claimed.
- Keep the existing slug, /data/homekind, pairing, selected entities, permissions, management opt-out and settings revision unchanged.
- Requires successful connector software checks and both architecture builds before publication; physical grouped-light behaviour and pilot installation remain separate verification steps.

# 0.18.0 — grouped-light release candidate, 18 October 2026

- Add an optional negotiated, one-use coordinated light lane. Check every locally selected member before one Home Assistant light action and keep partial or uncertain results distinct from confirmation.
- Persist intent and scoped receipts before authorisation; lost responses and restarts cannot repeat physical dispatch. Existing readings, individual controls and settings continue if this optional backend is unavailable.
- Retain the same app slug, data directory, pairing, local selection, settings revision and opt-out. No new routine Home Assistant checkbox or privileges.
- Requires the matched authorised HomeKind group backend and current room permissions. This candidate is not evidence of an installed pilot update, native radio broadcast, physical simultaneity or voice commissioning. The separate voice-light adapter remains unstarted pending the complete voice setup/pipeline release.

## 0.17.0 — 17 October 2026

- Send changed readings promptly without re-uploading every quiet device for each event. Keep a separate ten-second observation heartbeat so quiet readings stay current during busy periods.
- Reduce the upload burst floor from one second to half a second, within the existing 120 requests/minute bound.
- Separate media playback uploads, commands and favourite discovery. Live media changes wake the publisher; slow browsing or a control request cannot block playback readings.
- Reset Home Assistant reconnect backoff after a healthy session so later brief outages do not inherit an older thirty-second retry delay.
- Preserve the app slug, pairing, selected devices, settings revision, management opt-out and all existing permissions. No new feature checkbox or device reporting-rate change.
- P1 five-second detection/seven-second clearing is a separate local commissioning setting. Publishing this update does not configure or wake a physical sensor.

## 0.16.1 — 17 October 2026

- Offer the actual integer fan speed levels derived from its reported percentage step, including three-speed fans. Use the same levels in manual controls, routines and local dispatch validation.
- Retain the device families and consent/permission boundaries introduced in 0.16.0.

## 0.16.0 — 17 October 2026

- Added a guided Wi-Fi/network setup path and an in-app device support guide. Reuse confirmed discovery for devices already integrated through Wi-Fi, Ethernet, Bluetooth, Z-Wave, Matter or Thread; initial network/vendor commissioning remains integration-specific.
- Added curtains/blinds, fans and heating/cooling displays, supported controls and routine actions. Devices advertise their actual actions, modes, ranges, steps and units. Curtain position is 0% closed to 100% open. Stop remains available during a pending movement.
- Added light brightness where supported, and expanded environmental, utility and safety sensor classes. Added status and routine conditions for locks, access covers, valves, humidifiers, water heaters, alarm panels, sirens and mowers.
- Added routine reading fields for position, tilt, brightness, fan speed, current/target temperature, humidity, HVAC activity and recent button/doorbell events. Health remains excluded from household routines.
- New actions use the same expiring, single-dispatch leases and room permissions as existing controls. Validate actual local capabilities after authorisation; never retry an uncertain physical request or label stop physically confirmed.
- Preserve pairing, selections, management enrolment and feature choices. No extra Home Assistant feature checkbox. Bound richer upload batches to the existing API limits.
- Automated app, database, connector and architecture checks remain separate from physical commissioning.

## 0.15.0 — 17 October 2026

- Add supported devices from HomeKind: discover existing Home Assistant devices, or open a bounded joining window on a locally verified ZHA or Zigbee2MQTT network.
- Require explicit reading, name and room confirmation before expanding the local selection; preserve existing pairing and feature choices without another routine HA checkbox.
- Verify current sign-in, home administration, identity fingerprints and local settings again before pairing or enrolment. Cancelled or expired additions are paused and rejected completions roll back new local selections.
- Keep controls, camera viewers and personal health consent separate. Metadata contains no state values, media addresses or credentials.
- Keep the optional onboarding worker independent from ordinary readings. Recover interrupted enrolment receipts without repeating a pairing request.
- Automated UI, API, database, upgrade and container checks are separate from the required physical commissioning.

## 0.14.0 — 17 October 2026

- Added one-time local enrolment for managing feature choices in HomeKind.
- Retains pairing, sensor selections and existing permissions through upgrades.
- Validates bounded feature changes against the local selection and settings revision. Local changes invalidate pending requests.
- A separate settings worker keeps configuration outages from interrupting home readings and controls.
- Legacy installations stay under local management until explicitly enrolled. Personal health consent and approved camera viewers are unchanged.
- Software migration and permission checks are automated. Physical Green commissioning remains required. Automatic fleet rollout and rollback are separate work.

## 0.12.0

## 0.13.0 — Free radio and household favourites

- Discover free Radio Browser stations through Home Assistant, using the home's country and Australian presets where available.
- Play a selected station on an explicitly opted-in compatible media player. Station identifiers and stream addresses stay local; HomeKind receives only public labels and opaque keys.
- Keep station discovery independent of sensor updates and playback commands. Missing radio setup or an unavailable directory leaves existing controls working.
- Share up to three radio favourites across the household; changes use verified owner access in HomeKind.
- Preserve existing pairing, selected devices, Sonos favourites, health imports and light/plug control mode.

- Added a separate local media-player control choice, off by default.
- Compact room panel supports now playing, play/pause, volume and up to three favourites where supported.
- Media addresses and artwork stay in Home Assistant; track names are not written to history.
- Commands use expiring, permission-checked, one-time leases; media failures do not block sensors.
- Existing pairing, light/plug mode, camera choices and sensor selections are preserved.

## 0.11.0

- Added HomeKind-owned local CSV and JSON health-report imports, with explicit column mapping, preview and confirmation before creating HA readings.
- Kept only selected daily steps, sleep duration and resting heart rate; preserved file provenance and original dates.
- Kept Garmin passwords out of the app. Automatic Garmin sync is unavailable until approved API access is configured.
- Retained existing personal consent gates; expired local copies and handled source removal through Home Assistant outages.

## 0.10.1

- Aqara/Zigbee2MQTT buttons connect to their existing Home Assistant action inputs without a forwarding automation.
- Added triple press and native button action-name normalisation.
- Kept rapid presses as separate observations; ignored retained MQTT messages and duplicate legacy forwarding.
- Added direct button connection feedback and corrected press readings inherited from battery diagnostics.

## 0.10.0 — 17 October 2026

- Read selected vacuum cleaning/docked states and normalised physical button events.
- Offer a separate, explicitly selected button press input; its battery remains maintenance.
- Support stable MQTT device triggers through a local Home Assistant automation, with a fixed action allowlist and replay checks.
- Keep existing pairing, selections, weather, camera settings and read-only controls.
- HomeKind guided commissioning now records starting state, action and return; actual household tests remain to be completed.

## 0.9.0 — 16 October 2026

- Separate, opt-in camera allowlist; existing sensor selection and pairing are retained.
- Independent WebRTC signalling worker; media and camera credentials stay out of cloud storage.
- Viewer permissions, resident pause, expiry and access revocation are enforced server-side and locally.
- The pilot camera database and endpoint are active. Physical camera and Green commissioning is still required.

# Changelog

## 0.8.0 — 16 October 2026

- Share locally selected Home Assistant weather sources, including BOM integrations.
- Show current conditions, temperature and humidity in HomeKind Local information.
- Keep forecast history, location attributes, media and provider tokens local.
- Retain pairing, device selections, light controls and fast updates.

## 0.7.0 — 16 October 2026

- Discover camera status, event and binary doorbells, and supported numeric health sensors.
- Strip camera/video access tokens and URLs; live video stays in Home Assistant.
- Report real doorbell rings without replaying old timestamps or repeated heartbeats.
- Upload personal health values only for an explicitly connected, consenting person.
- Keep personal health separate from household devices, history and summaries, with category sharing and seven-day latest-reading expiry.
- Retain existing media readings, light controls, pairing, selections and fast updates.

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
