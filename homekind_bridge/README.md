# HomeKind Home Connector

## Add devices in HomeKind (0.15.0)

After an installer updates this same connector and the home is enrolled in **Manage feature choices in HomeKind**, use **HomeKind → Settings → Devices → Add device**. Existing enrolled homes need no additional feature checkbox. Pairing credentials, existing selections and feature choices survive the update.

- **Find connected devices** searches supported devices already in Home Assistant.
- **Pair a new Zigbee device** is available for an existing ZHA network, or a Zigbee2MQTT network whose exact permit-join route can be verified from Home Assistant's local MQTT discovery. Joining is limited to 90 seconds; follow the device manufacturer's pairing instructions. A missing network, vendor account, Wi-Fi or Bluetooth integration can still need an installer in Home Assistant. This does not implement Matter or arbitrary integration setup.
- Review a found physical device, select its readings, confirm names and rooms, then explicitly add them. Metadata is shared only during the expiring setup session; readings are not added to the local selection before confirmation. Existing local selections are retained.
- Wait for the home connector's acknowledgement and check the first real reading. Confirmation does not mark a device Tested or Active. Controls and live cameras remain separate choices in HomeKind; health devices stay **On person** and require the person's own connection and sharing choices in **My sharing**.

Setup requires a current verified account with both home-connection administration and whole-home device-setup access. Requests expire after five minutes or the requesting sign-in's expiry. Cancelling, changing local settings, replacing the connector or losing permission stops further setup; the radio also has its own short timer. An interrupted or expired addition is paused, and a rejected completion rolls back only its newly appended local selections when the connector next checks in. Closing a confirmed addition leaves its acknowledgement in progress; closing an unconfirmed search cancels it.

The connector runs setup independently from ordinary readings. It accepts a typed session, a known local Zigbee adapter and explicitly approved entity fingerprints. It does not accept arbitrary Home Assistant services, MQTT topics, credentials, code or integration configuration from the cloud. No extra Supervisor privilege is required. Software tests cover the protocol and permissions; physical Green, radio, device, iPhone and iPad commissioning is still required.


## Feature choices in HomeKind (0.14.0)

During installation, agree the selected devices with the resident and enable **Manage feature choices in HomeKind** once. Afterwards, use **HomeKind → Settings → Devices → Connection settings → Connection choices** for lights/plugs, media and live-camera availability. Existing device choices and credentials are retained on an update. Existing installations stay locally managed until explicitly enrolled. Camera viewers and personal health consent remain separate choices in HomeKind.

Routine updates must keep the saved settings. An installer handles new device installation, the agreed automatic app update policy, backups and recovery. This version does not enable fleet software updates or rollback itself.

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


## Live camera viewing (0.9.0)

Update this same connector installation to **0.9.0**, then open its Web UI. Keep the camera selected in readings and also tick it under **Live camera view**. Save. Pairing and existing selections are retained. Live viewing remains off until this separate local choice is enabled.

In HomeKind, open the camera in its room, choose **Live-view choices**, enable viewing and select approved people. An approved person can then choose **View camera**. The resident can pause sharing. Video is receive-only, sound starts muted, and closing or hiding the view stops it. Sessions end after five minutes.

The cloud camera services are active. Actual G100 playback, iPad behaviour and outside-home connectivity still require commissioning. No relay credentials are configured for this pilot. See the Documentation tab for the complete checks.


### Media controls (0.12.0)

In the connector Web UI, keep a TV or speaker selected for readings, then opt it into **Media controls & now playing**. Save without changing your pairing code. In its HomeKind room, open **Media choices**, enable permitted controls and select up to three favourites. Basic controls are shown only when supported. Favourites use the player's provided favourites/source list, with media addresses retained locally. Updating alone does not enable media controls or change light/plug control mode. Physical playback is not yet commissioned.

### Free radio (0.13.0)

Update and restart the connector, keeping your existing media selections and pairing. Add **Radio Browser** once under Home Assistant **Settings → Devices & services → Add integration**. Compatible players then offer **Radio** in HomeKind. The verified household owner can tap a heart to save up to three favourites, shared across the household. Australian homes prioritise ABC Radio Melbourne, ABC Classic and ABC Jazz when listed by Radio Browser; other stations remain browsable. Existing player favourites remain available. No radio account, API key or HomeKind subscription is required for the directory. Internet and a compatible player are required for playback.

Discovery uses Home Assistant's authenticated `media_source/browse_media` WebSocket API in a separate worker. The connector retains station identifiers locally and resolves opaque playback requests only against its discovered catalogue. No stream URLs or arbitrary media addresses are accepted from HomeKind.
