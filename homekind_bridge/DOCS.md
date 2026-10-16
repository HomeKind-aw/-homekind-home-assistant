# HomeKind Home Connector 0.9.0

An installable Home Assistant app for the HomeKind pilot. Setup opens inside Home Assistant on your phone: paste a pairing code, tick the sensors you choose and connect. It reuses the existing HomeKind bridge service and runs on your Green.

For current weather, select your `weather` source in the connector Web UI and save. Conditions, temperature and humidity appear in HomeKind Local information without room allocation. A BOM integration can supply these readings. Weather follows local-information permissions; forecasts, source credentials and location attributes remain local.

## Install from the Home Assistant App store

1. Open Home Assistant using an administrator account.
2. Open **Settings → Apps → App store → ⋮ → Repositories** and add the URL of this GitHub repository. Earlier Home Assistant versions call Apps **Add-ons**. HomeKind's **Settings → Devices → Connect Home Assistant** screen will show the repository link once it has been published.
3. Install **HomeKind Home Connector**, enable **Start on boot**, and start it. The first installation builds the pinned container on your Green and needs internet. No pairing code is needed to start the setup screen.
4. Tap **Open Web UI**. Leave this screen ready on your phone.
5. Open your HomeKind household, then **Settings → Devices → Connect Home Assistant → Create pairing code**. Copy the fresh code; it lasts 10 minutes.
6. Return to the HomeKind Web UI inside Home Assistant. Paste the code, tick your intended sensors or device states, then tap **Connect household**. No file copying, terminal command or Home Assistant password is needed for this route.
7. In HomeKind → **Settings → Devices → Add device**, add each discovered sensor and select its room. Exact area-name matches can suggest a room; confirm the actual room before saving.

If GitHub publication has not completed, this package is still suitable for a computer-based local install: copy `homekind_bridge` into Home Assistant's `local_apps` directory, refresh the App store and install it under Local apps. Use **Open Web UI** for the same setup steps. This local-file route requires a computer; it is not the phone installation route.


## 4. Map the discovered entities to the existing rooms

In HomeKind → Settings → Devices → Add device, choose a discovered entity, confirm its type and name, choose its actual room from the imported plan and save it as Paired. Repeat for the five required entities. Exact HA area-name matches can suggest a room; the owner confirms the assignment by saving. Areas that do not exactly match, including an ambiguous Living room, require manual selection.

Home Assistant unique ID, platform, device ID and area are retained when the local account allows registry reads. These are setup identifiers, not permission authority. Registry access may be unavailable for a non-admin HA user; entity IDs still work. Renaming an entity requires reviewing and saving its binding in HomeKind and updating the local allowlist. No automatic reassignment to another room is performed. Do not mark a device Tested or Active until the local checks below pass.

Open Home, Your rooms or the home plan to inspect the same permitted readings. The home plan uses the imported room boundaries and model parts. Read-only mode hides control buttons and blocks server-side command requests, leasing and dispatch. Existing command machinery remains available for a separately authorised later phase.

## 5. Commission the actual devices

Record results in PILOT_ACCEPTANCE.txt. Watch the HomeKind screen as well as Home Assistant.

- Walk into and out of the selected presence zone; note the sensor's own clear delay. No motion does not establish that a room is empty.
- Open and close the contact. Confirm that the correct room changes.
- Test the leak sensor using its manufacturer's safe test procedure and confirm its reset. Keep history/notifications disabled during this initial read-only test unless the household explicitly chooses them.
- Use a small non-essential appliance to change the Tapo watts reading. Do not interrupt a fridge, heater, medical device or other essential load.
- Change the bulb using its physical/local Home Assistant control; confirm that HomeKind only reads the result.
- Record physical action time, HA update time and visible HomeKind update time for several changes. Do not use the synthetic test measurement in the results report as a physical pilot latency.
- Stop this connector or temporarily disconnect Green's Ethernet safely. A disconnected socket is reported as disconnected; a missed heartbeat/reading expires after 45 seconds, plus the next app refresh. Last-known data must remain labelled as such. Restore the connection and confirm recovery.
- Restart the connector, then restart Home Assistant when convenient. Verify a fresh snapshot and Start on boot. Check the iPhone and iPad layouts.
- Test owner, room-limited family member and a different household account. The family member should see only granted rooms; the different household should see none. Use Stop connection and confirm subsequent sync is rejected before pairing again if needed.

No actual device is commissioned by automated software checks. Do not enable unattended high-stakes alerts based on this connection test.

## Delivery and recovery behaviour

The connector subscribes to `state_changed` before taking a REST snapshot, merges by HA timestamps, uses WebSocket ping/pong to detect loss, and reconnects with backoff and a fresh snapshot. Changes wake the uploader, with bursts combined into at most one upload per second; otherwise it sends a ten-second heartbeat. Version 0.5.0 handles commands in a separate worker, checking for them every half second between requests, so sensor uploads and slow device confirmations do not block one another. Up to three leased commands can run together. HomeKind uses private live update signals and fetches the permitted readings when they change. Immediate “Sending” feedback is separate from the actual reported device state. Recovery polling remains available if the live connection drops. Actual latency still depends on Home Assistant, each device’s reporting interval and the network, and must be measured locally.

A private on-disk latest-state outbox retains at most 250 selected entities for at most 24 hours. It coalesces repeated changes per entity; it is not a complete recording of transitions during an outage. A reconnect snapshot replaces pending values with the currently observed state. A disconnected or old queued sample is never labelled as a new current observation. Deterministic delivery IDs and the existing ingestion ledger prevent duplicate processing. Backend deduplication IDs older than seven days are removed on the next sync for that household; they contain no raw sensor values. State-change time, observation time and server receipt are distinct.

The database refuses future/invalid timestamps, non-numeric environmental readings, unsupported classes, cross-household bindings and command access in read-only mode. Authenticated sync is limited to 120 requests/minute per bridge. Revoked bridge credentials stop further sync. The connector logs connection state and counts, not tokens or sensor histories.

## Another always-on local computer

Use Python 3.11+ and install `homekind_bridge/requirements.txt` into a virtual environment. Create a dedicated **non-admin** Home Assistant user and a long-lived token for it. HA tokens generally follow the user's permissions and are not fine-grained per-entity scopes; the explicit local allowlist remains required. Registry metadata may be denied to this user.

Create an owner-only options JSON locally containing `pairing_code`, `read_only: true`, `entity_ids: [...]` and `ha_url: http://homeassistant.local:8123`. Run:

```sh
python3 -m venv .venv
.venv/bin/pip install -r homekind_bridge/requirements.txt
.venv/bin/python homekind_bridge/bridge.py --options homekind-options.json --data homekind-credentials
```

The token is entered at a hidden local prompt and saved with owner-only file permissions. Do not put it in shell command history. To run unattended, configure that computer's service manager to start the same command at boot as an unprivileged user with a private working/data directory. Use TLS when the local connection crosses an untrusted network. The Green app path above is the default for this pilot.

## Rollback and troubleshooting

- Stop or uninstall the local connector and use HomeKind → Stop connection to revoke credentials. Existing local HA devices, Zigbee2MQTT and automations continue independently.
- If the app will not appear, check the local folder and the presence of all four files, refresh the app store and read its build log. Do not share raw configuration secrets.
- Pairing failure: check the ten-minute expiry, owner MFA, whether an old connector was revoked and the Green's clock. Create a fresh code if necessary.
- No catalog: confirm the exact local entity_ids allowlist, supported device classes and HA availability. No values: also register the entities in HomeKind as Paired and map their rooms.
- Clock errors: correct the Green/computer clock; do not weaken server checks. Internet failure: local devices still work; HomeKind displays the last known reading until a fresh observation arrives.
- To roll back this software release, deploy the previous saved private Site version and bridge v5 source from the recorded source commit. The repository contains `database/rollback_home_assistant_streaming.sql` to restore prior RPC definitions. Stop/revoke 0.2 connectors first; the rollback intentionally disables the new sync overload and leaves additive columns intact. Reverting the old connector also reverts to polling and its previous control capability; keep it stopped unless explicitly required.

## Reference documentation checked 14 October 2026

- https://developers.home-assistant.io/docs/apps/tutorial/
- https://developers.home-assistant.io/docs/apps/communication/
- https://developers.home-assistant.io/docs/api/websocket/
- https://developers.home-assistant.io/docs/api/rest/
- https://websockets.readthedocs.io/en/16.0/reference/sync/client.html
- https://supabase.com/docs/guides/functions/auth-headers


## Optional light and plug controls (0.4.0)

After updating, open the connector Web UI and enable **Allow light and plug controls**, then save. Read-only is the default. Current pairing and selected devices are retained. In HomeKind, enable controls per device under **Display & alerts**. HomeKind groups can request all enabled lights in a room together; every member is authorised separately and its actual state is reported back. Only selected light and switch entities can be controlled.


## TV and audio activity (0.6.0)

Update the connector, restart it, and open its Web UI. Select your media players alongside existing sensors and save. Pairing and your current selections are retained. In HomeKind, add the found TVs or speakers and allocate them to rooms. They appear as small readings such as “TV · Playing” and “Speaker · Paused”, with last-use history when recording and access allow it. “On” without playback detail is shown as “Playback unknown”; unavailable devices retain a clear status. Programme titles, artists, artwork and media URLs are not sent. This update reads playback state; media service controls are not enabled. Ordinary playback changes do not generate a notification.


## Cameras, doorbells and personal health (0.7.0)

Update the HomeKind Home Connector to **0.7.0**, restart it, then open its Web UI. Existing pairing, selected entities and light/plug control choices stay in place. Select your cameras, doorbell entities and supported personal sensors, save, then add the found devices under **HomeKind → Settings → Devices**.

- **Cameras:** small status readings such as Ready, Streaming, Recording or Off. Images, video, camera access tokens and stream URLs stay in Home Assistant. Live video is not relayed by this connector.
- **Doorbells:** supported `event` entities and binary doorbell sensors show their ring state. New rings can appear as “Front doorbell rang” in recorded home activity. The initial snapshot, an old event timestamp and repeated heartbeats do not create new ring events. Doorbell updates do not generate a notification for every reading.
- **Health:** steps, sleep duration, resting/current heart rate, blood oxygen, weight, body temperature, systolic/diastolic blood pressure and blood glucose are recognised from explicit metric names/classes and matching units. Other arbitrary sensors and personal text are excluded. Generic room temperature stays a home reading.

Allocate health sensors to **On person**, then each person opens **My sharing → Health readings → Connect a personal sensor → Connect to me**. A source belongs to one person at a time. This enables collection for its health category; existing sharing choices for that category apply. Collection begins with the next report from Home Assistant. Until this personal connection is enabled, the connector sends discovery metadata only, with no health values uploaded.

Health values live in a separate protected workspace. Household ownership and device access do not grant access to another person's readings. Steps, sleep and resting heart rate each have a sharing category; the remaining metrics use **Other health readings**. Choose recipients or keep a category for yourself. Personal readings appear in your private home panel and My sharing, never in shared room metrics, household activity, summaries or per-reading notifications. Pausing the category or disconnecting the source stops collection and clears the saved reading. Only the latest report is retained, expires after seven days, and is cleared by the minute maintenance job. The displayed timestamp is when Home Assistant reported the value, not a claim about when a measurement was physically taken.

No new camera, doorbell or health service controls are added. Light and switch On/Off controls retain their existing local selection, per-device permission and confirmation checks.


## Live camera viewing — 0.9.0

This build adds a separate **Live camera view** choice in the connector. Camera status continues through the existing sensor connection. Live view is off by default, including after an update.

Update this same connector installation, open **Web UI**, keep the camera selected in readings, then tick it under **Live camera view** and save. Pairing and existing controls are retained. In HomeKind, open the camera in its room and choose **Live-view choices**, enable viewing and select approved people. An approved person can then choose **View camera**. The resident can pause live camera sharing for the home without gaining access to an unshared feed.

The connector carries short-lived WebRTC connection messages, not video, images, RTSP addresses or Home Assistant credentials. Video is receive-only, sound starts muted, and no webcam or microphone permission is requested. Closing the view, leaving the page or hiding the app stops the viewing session. Sessions end after five minutes; open the camera again to continue.

Camera failures use a separate worker and do not stop the sensor stream or light/plug command worker. An additional network relay may be needed for viewing away from home. Relay support is implemented but this pilot has no relay credentials configured. Actual G100 video/audio, iPad playback and outside-home viewing remain uncommissioned. The runtime API was checked against Home Assistant Core 2026.10.1.

The pilot camera database, dedicated endpoint and HomeKind viewer are active. Update and commission the same Green installation before relying on live viewing. Physical G100 playback is not yet verified.

## Physical buttons — Connector 0.10.1

1. In this connector's Web UI, select the button's separate **press** reading and save. Keep your pairing code blank when already paired.
2. For Aqara buttons connected through Zigbee2MQTT, the connector reads the existing Home Assistant device-trigger discovery and subscribes to that button's exact action topic through Home Assistant. **No new automation or broker credentials are needed.**
3. Check the button connection message in this Web UI. If an action has not appeared yet, perform it once in Home Assistant. Discovery refreshes every 30 seconds. Supported inputs are single, double, triple, hold and release, where the device exposes them.
4. Add the press reading in HomeKind → Devices, assign its room or On person, then open Settings → Pilot checks. Test the physical actions and check their displayed names. The guided repeat-press session confirms two fresh events; it does not certify every action automatically.

Only explicitly selected button sources are inspected. Topic names, MQTT payloads and diagnostic logs remain local; HomeKind receives the fixed action and observation time. Wildcard topics, arbitrary templates, retained MQTT messages and unsupported actions are rejected. A battery update never creates a press. Rapid presses remain separate observations in the bounded local outbox.

Native Home Assistant button event entities are also supported; choose that existing reading when available. A previously configured `homekind_button` forwarding automation remains a compatibility fallback only when a direct MQTT input is not active. Once the direct input connects, forwarded events for that source are ignored to avoid duplicates. Existing forwarding automations can then be disabled.

The connector uses the existing local Home Assistant account and permissions. If HA refuses the MQTT subscription, the Web UI reports that the button input is not connected; ordinary sensors keep working. No permission is widened automatically. This records button activity only; family notifications or other routines must be configured separately.

Vacuum status is read-only. Select the actual `vacuum` entry to share Cleaning, Docked, Returning or Paused. Battery and dock settings do not establish cleaning activity.


## HomeKind-owned health import — 0.11.0

Open this app's Web UI → **Import personal health readings**. This first version is a manual report importer, not automatic Garmin synchronisation. It contains no HACS integration, Garmin login implementation or additional third-party Python dependency. Building our own app does not make Garmin's private login endpoints an approved or stable integration; supported automatic Garmin access still needs OAuth credentials, the approved API documentation and Garmin approval/licensing where applicable.

For Garmin, export a daily report from the Garmin Connect website → Reports → choose the metric/time range → Export. This importer supports a single CSV header row with full dates (`YYYY-MM-DD`, `DD/MM/YYYY` or `MM/DD/YYYY`). Map the date column and only the metrics you want. Reports using other layouts/date formats must be converted into the blank template first. The template has `date,steps,sleep_minutes,resting_heart_rate`. HomeKind JSON accepts an array with these same keys, or an object with a `readings` array. Garmin activity lists, ZIP archives, FIT binary files and raw whole-account JSON exports are not supported in this version. Real account-specific export layouts still need a pilot test.

1. Create a source label for one person, choose the provider and the time zone of the report's daily dates. Keep separate sources for different people.
2. Select a CSV or HomeKind JSON file up to 128 KB. Map the CSV columns, date format and sleep unit. Empty metric selections are excluded.
3. Preview the latest selected reading for each metric, including its source date. Conflicting same-day values, future dates, invalid numeric values and unsupported files are rejected. Older rows are excluded; only the last seven calendar days are eligible.
4. Confirm that the selected values can be stored in this Home Assistant home. They become HomeKind-owned `sensor.homekind_health_...` current states. No device control service is called. HA may record these states and include them in its own backups according to its settings; administrators must manage that retention in HA.
5. Reload the sensor list above, select the new personal sensors, and save. In HomeKind, add the found health sensors in Devices, allocate them to On person, then connect each one to yourself in My sharing → Health readings. Existing collection and recipient choices continue to apply. Creating an HA reading does not opt anybody into HomeKind collection.

Raw reports are processed in memory only and are not saved or sent to HomeKind. Preview data expires after ten minutes. This importer saves only each metric's latest date/value in the app's private data directory (directory 0700, file 0600); it retains no Garmin password or token. Up to five named local sources are supported. The local file store expires readings after seven calendar days. Clearing a source replaces its current HA values with unavailable, with a persistent cleanup queue if HA is offline. This does not purge HA recorder history or backups.

The imported readings are republished to HA every minute so they survive an HA restart. Their original source date never changes. The personal HomeKind display explicitly identifies the file source and its original date; a fresh HA report timestamp is not a new physical measurement. HomeKind separately enforces expiry using that source date, and collection pauses/removal retain the protected personal workspace's existing access rules. File imports are excluded from household summaries, alerts and general home activity. No live Garmin connection or real-device test is implied by passing software tests.

References: https://support.garmin.com/en-IN/?faq=W1TvTPW8JZ6LfJSfK512Q8 and https://developer.garmin.com/gc-developer-program/health-api/
