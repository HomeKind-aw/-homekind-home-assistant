# HomeKind M5Stack room speaker pilot

Prepared **5 November 2026** for one **Atom VoiceS3R**, previously **Atom EchoS3R**,
part **C126-VOICE / C126-ECHO**. The original Atom Echo is different hardware.
Home Assistant Green remains the home integration layer.

This is a prepared ESPHome Assist endpoint, **not an activated Ask HomeKind
hands-free service**. Hardware arrival and commissioning remain necessary.

| Function | Current position |
| --- | --- |
| Microphone, speaker, on-device wake word | Manufacturer firmware with HomeKind installation overlay |
| First wake word | **Okay Nabu**, a labelled development wake word |
| First boot | Muted until commissioning with the resident |
| Short button press | Start/stop one request while unmuted; stop a ringing timer |
| Hold at least 1.5 seconds and release | Toggle software microphone mute |
| Pairing and wireless updates | Encrypted with a unique locally generated device key |
| Status | Home Assistant mute switch and Voice readiness entity |
| Replies, announcements and timers | Through the selected, commissioned Assist pipeline |
| Ask HomeKind facts, help and HomeKind routines | Matched backend and endpoint integration still required |
| “Hey HomeKind” / sounds of distress | Neither implemented nor verified by this package |

This model has **no RGB indicator** and no physical microphone disconnect
switch. The button operates software mute; unplugging disconnects power. Agree
the room, listening times and permitted requests with the resident before use.

## Have ready

- The exact device above, a **USB-C data cable**, and an Australian USB power
  adaptor. A charging-only cable cannot perform the first USB flash.
- The existing Green, **2.4 GHz Wi-Fi**, and a computer for first installation.
- The agreed room and a comfortable position clear of the TV and grille
  obstructions. Measure that room before buying further units.

## Installer setup

1. Check the Green's supported Core/OS version and normal backup/update plan.
   Add **ESPHome Device Builder**, **Speech-to-Phrase** and **Piper** through
   **Settings → Apps** (Add-ons on older releases). Start the speech services
   and add their discovered **Wyoming Protocol** integrations. This package
   requires **ESPHome 2026.10.2**. No script here updates the Green.
2. In **Settings → Voice assistants**, create **HomeKind room pilot** with English
   input, **Home Assistant** as the initial conversation agent,
   **Speech-to-Phrase** recognition and **Piper** output. Choose an Australian
   English voice if available and verify the actual installed voice. The device
   detects its wake word locally; openWakeWord is not required.
3. Copy this folder, including `upstream/`, into the local ESPHome configuration
   directory. Generate private installation values locally, or use ESPHome's
   own private secrets editor. Keep Wi-Fi/device keys out of Git and chat.
   Preserve the same secrets file through upgrades.

   ```sh
   python3 -m venv .voice-venv
   .voice-venv/bin/python -m pip install -r requirements.txt
   .voice-venv/bin/python scripts/create_secrets.py
   .voice-venv/bin/esphome compile homekind-atom-voices3r.yaml
   .voice-venv/bin/esphome upload homekind-atom-voices3r.yaml --device /dev/ttyACM0
   ```

   Replace the serial port with the connected device's actual port. Alternatively,
   download the compiled **factory image** from ESPHome and install it using
   [ESPHome Web](https://web.esphome.io/) in Chrome/Edge with the USB data cable.
   Safari does not provide that Web Serial route. Follow M5Stack's current USB
   download-mode instructions if the port does not appear. No soldering is needed.
4. Add the discovered ESPHome device in Home Assistant. Enter its unique
   `api_encryption_key` from the private local file, assign the agreed room and
   select **HomeKind room pilot** in the device's **Voice Assistant** settings.
5. Confirm **Microphone Mute** is on after first boot. With the resident, hold
   and release to unmute and check the actual switch/readiness status. Try
   **“Okay Nabu, what time is it?”**, then one supported command for an explicitly
   agreed, exposed device. Native Assist tests do not establish HomeKind access.
6. Run the read-only check and complete [ACCEPTANCE.md](ACCEPTANCE.md). Keep
   listening paused if the agreed functions are not ready.

Private secret names are `wifi_ssid`, `wifi_password`, `api_encryption_key`
(random base64-encoded 32 bytes) and `fallback_ap_password`. OTA uses the same
device key. The generated firmware contains installation credentials: keep the
private file and compiled binary private. CI uses public synthetic values for
compilation checks only; **never install its test binary**.

Speech-to-Phrase is a limited command recogniser, suited to initial Green tests.
It does not transcribe arbitrary Ask HomeKind questions. Open-ended conversation
needs a suitable transcription service plus the HomeKind agent. Whisper on
Green may be slow; measure latency before selecting it for the resident. A local
pipeline does not require a new cloud AI credential or subscription.

## Read-only connection check

From the home network, enter the local installer's authorised Home Assistant
token at the hidden prompt. Do not post a token in chat.

```sh
.voice-venv/bin/python scripts/preflight.py --ha-url http://homeassistant.local:8123
.voice-venv/bin/python scripts/preflight.py --ha-url http://homeassistant.local:8123 --device-id THE_ACTUAL_REGISTRY_ID
```

The check reads configuration and registries, lists candidate device IDs and
requires explicit identity selection. It does not call a service, change a
setting, read sensor states, capture audio or save a token. Confirm the device's
selected pipeline manually. A registry entry cannot prove recognition/playback.

## HomeKind hand-off and remaining work

The public connector remains **0.25.0**. This package changes no connector runtime,
pairing, device selection, management choice or `/data/homekind` content. Native
Assist preparation does not require a connector upgrade or feature checkbox.

The existing private HKQ-003 design uses metadata-only protocol 1 for `help`,
`room_lights_on` and `checkin_okay`. Before enabling those, finish the matching
authenticated backend/collector and conversation adapter, register the real
arrived endpoint, bind its room/resident/current consent and chosen recipients,
and verify delivery feedback. **Hardware arrival alone will not complete those
software tasks.** The earlier source remains the starting point; do not silently
substitute ordinary Home Assistant permissions for HomeKind's checks.

A room microphone cannot identify its speaker. Never copy an app sign-in or AI
provider key into the room speaker, impersonate the owner, or describe queued
help as heard by family. Full Ask HomeKind conversation, a custom wake word and
distress detection each require further implementation and acceptance.

For recovery, retain the private device keys and use a trusted USB reflash. The
maker's 10-second factory reset gesture is replaced by the mute gesture. A
factory erase requires pairing, room and consent checks again.

## Source and verification

`upstream.lock.json` identifies the exact MIT-licensed manufacturer YAML and
its external audio stack. The original notice is retained in `upstream/`.
Upstream sound/wake-word assets are moving dependencies; this is not a fully
locked supply-chain build. Software evidence is in `VERIFICATION.md`.

- [Manufacturer Assist guide](https://docs.m5stack.com/en/homeassistant/applications/voice_assistant/atom_echos3r)
- [Local Assist setup](https://www.home-assistant.io/voice_control/voice_remote_local_assistant/)
- [On-device wake words](https://esphome.io/components/micro_wake_word/)
- [Encrypted updates](https://esphome.io/components/ota/esphome/)

No physical microphone, speaker, custom wake word, distress or HomeKind delivery
result is established by a firmware compile or software test.
