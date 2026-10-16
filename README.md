# HomeKind Home Connector 0.3.0

An installable Home Assistant app for the HomeKind pilot. Setup opens inside Home Assistant on your phone: paste a pairing code, tick the sensors you choose and connect. It reuses the existing HomeKind bridge service and runs on your Green.

## Install from the Home Assistant App store

The connector repository is public so Home Assistant can download and install it directly from your phone. The HomeKind portal, database, household data and credentials remain private. You do not need a GitHub sign-in to install the connector.

1. Open Home Assistant using an administrator account.
2. In HomeKind, open **Settings → Devices → Connect Home Assistant → Add HomeKind to Home Assistant**. Alternatively, open Home Assistant's **Settings → Apps → App store → ⋮ → Repositories** and add `https://github.com/HomeKind-aw/-homekind-home-assistant`. Earlier Home Assistant versions call Apps **Add-ons**.
3. Install **HomeKind Home Connector**, enable **Start on boot**, and start it. The first installation builds the pinned container on your Green and needs internet. No pairing code is needed to start the setup screen.
4. Tap **Open Web UI**. Leave this screen ready on your phone.
5. Open your HomeKind household, then **Settings → Devices → Connect Home Assistant → Create pairing code**. Copy the fresh code; it lasts 10 minutes.
6. Return to the HomeKind Web UI inside Home Assistant. Paste the code, tick your intended sensors or device states, then tap **Connect household**. No file copying, terminal command or Home Assistant password is needed for this route.
7. In HomeKind → **Settings → Devices → Add device**, add each discovered sensor and select its room. Exact area-name matches can suggest a room; confirm the actual room before saving.

For an optional computer-based local install, copy `homekind_bridge` into Home Assistant's `local_apps` directory, refresh the App store and install it under Local apps. Use **Open Web UI** for the same setup steps.

## Update an existing installation

The `homekind_bridge` app slug and `/data/homekind` credentials directory are unchanged. A local installation and a repository installation have different Home Assistant repository identifiers, so use **Stop connection** in HomeKind before moving between them and pair the replacement once. An update within the same app installation preserves its credentials. Do not run two connectors for the same household.

After updating to 0.3.0, open **Web UI** to manage the sensor selection. That screen's saved selection becomes authoritative; it is kept in the app's private data directory. You can remove every tick and save to pause local sharing, or select sensors again to resume. A refused/expired code or revoked bridge requires a new pairing code; it is not retried in the background. A completed pairing no longer needs its code entered on future starts.

## Household and room setup

Use your own private ChatGPT sign-in for HomeKind. The named pilot testing account does not require an authenticator; other accounts retain their normal checks. Create or open your household with its actual time zone. In **Settings → Rooms & plan**, import the saved pilot if needed. **Rename** keeps a room's identity, model mapping and device assignments. **Delete** removes the room from home views, ends its scoped access and open invitations, and moves devices to Unassigned with controls disabled. The model and historical records are retained.

Choose the intended coverage with the resident before enabling it. History, notifications and routines are separate choices; the connector does not turn them on. Readings describe sensors, without identifying a person or proving that a room is empty.

## Security and verification

- The Web UI is available through Home Assistant Ingress to an administrator. The app accepts Ingress traffic from the Supervisor only and exposes no host port.
- Pairing and bridge credentials remain in the app's private data directory. The cloud stores credential digests. The UI never returns the Supervisor token, bridge token or retained credential file.
- The phone setup starts in read-only mode. A local administrator can explicitly allow selected light and plug controls. Microphones, person/location entities, raw camera/video attributes and unclassified personal text are excluded. Cameras share status only; supported health values require a separate personal connection and consent. Discovery sends only the selected supported entity metadata; reading values also require registration in the HomeKind household.
- Home Assistant's `homeassistant_api` capability is broader than a read-only per-entity scope. Code and the explicit local allowlist enforce the restriction. The app requests no Supervisor management, host network, Docker access, privileged mode or extra host mounts.
- The existing bridge retries temporary outages, reconnects with a snapshot, and labels stale readings. It logs connection status and counts, without raw credentials or sensor histories.
- Run the physical checks in `PILOT_ACCEPTANCE.txt`. Automated software checks do not commission your actual Green, sensors, iPhone or iPad.

Detailed behaviour and local computer instructions are in [DOCS.md](homekind_bridge/DOCS.md). The repository has no household floor plan, account data or service-role key.

Bundled Inter and Nunito fonts retain their SIL Open Font License notices in `homekind_bridge/OFL-Inter.txt` and `homekind_bridge/OFL-Nunito.txt`, including inside the built app container.


## Optional light and plug controls (0.4.0)

After updating, open the connector Web UI and enable **Allow light and plug controls**, then save. Read-only is the default. Current pairing and selected devices are retained. In HomeKind, enable controls per device under **Display & alerts**. HomeKind groups can request all enabled lights in a room together; every member is authorised separately and its actual state is reported back. Only selected light and switch entities can be controlled.


## Faster updates (0.5.0)

Update the HomeKind Home Connector in Home Assistant and restart it. Pairing, selected devices and your current control choice are retained. Commands and sensor uploads now run independently, with up to three commands at once. Readings wake the uploader; heartbeat writes are batched. HomeKind receives live update signals and shows “Sending” immediately, then the device’s reported result. Devices that report infrequently and slow networks still affect the final timing.


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
