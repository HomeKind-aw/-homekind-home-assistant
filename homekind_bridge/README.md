# HomeKind Home Connector

Selected sensor readings from your Home Assistant Green, in your HomeKind home view.

## Current weather (0.8.0)

Update to **0.8.0**, restart the connector, open its Web UI and select your `weather` source alongside your existing selections. Save once. Current conditions, temperature and humidity then appear automatically in **HomeKind → Local information**; weather does not need room allocation. A BOM source already connected to Home Assistant is preferred when available.

Only selected current-weather fields are shared. Forecast history, provider tokens and location attributes stay in Home Assistant. Existing local-information permissions apply. Pairing and previous device selections are retained.

Install, start, then tap **Open Web UI** to paste your HomeKind pairing code and select sensors on your phone. The connector starts in read-only mode and uses your local Home Assistant session. Its private credentials stay on the Green.

Your HomeKind household controls who can see each room. Device registration, history, alerts and notifications are separate choices in HomeKind.

This pilot app supports Home Assistant OS on aarch64 and amd64. Physical Green and phone commissioning is required; see the Documentation tab.


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
