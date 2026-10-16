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
