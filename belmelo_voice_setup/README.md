# Belmelo Voice setup — kitchen pilot

This one-time app installs the `belmelo_voice` custom integration. It connects an existing ESPHome Assist microphone to the scoped Ask Belle room voice route. It does not change microphone firmware, install an AI key on the Green, change the default assistant, or replace the Belmelo home connector.

1. Add this repository in the Home Assistant App store if it is not already present. Install **Belmelo Voice setup**, start it and open its web UI. Choose **Install integration**.
2. Restart Home Assistant once. In Belmelo, open **Ask Belle → More options → Room microphones**. Choose the room, selected lights and any shared plans/readings. Create a pairing code.
3. In Home Assistant **Settings → Devices & services → Add integration**, choose **Belmelo Voice**. Paste the ten-minute code and choose the matching ESPHome microphone. Pairing creates the dedicated Belle assistant and attempts to select it on that microphone. If Home Assistant shows a finish-setup notification, follow it. All three engines must be Belmelo Voice, and local command handling must be off.
4. Keep the existing wake word. Test “Turn off the lights in the kitchen”, “Kitchen lights on”, “Could you please switch off the kitchen lights?” and “Turn them back on”. Check the physical result and whether Belle reports confirmed or still pending. Test an ambiguous “Turn kitchen on” followed by “yes”.
5. Test an unselected room, pause access in Belmelo, and verify that neither operates lights. Check only household-shared plans are spoken.

The setup app can be uninstalled after the integration is loaded. Its only writable map is Home Assistant configuration. It writes its own integration folder and keeps a backup of a previous version under its own `/data`. It never restarts Home Assistant automatically. The selected microphone binding is stored in the HA integration entry; the cloud stores a hash. No personal app session or OpenAI key is stored on the microphone.

Scope: selected lights on/off, ambient room temperature/humidity, household-shared plans today/tomorrow and short clarifying follow-ups. Personal health/appointments, arbitrary services, routines and custom “Hey Belle” wake words are outside this pilot. Cloud transcription replaces Speech-to-Phrase for this dedicated assistant. Short requests up to thirty seconds are supported. Internet and the existing Belmelo AI service are required.

Software checks use fictional households and stubbed HA runtime contracts. Physical Green installation, microphone audio quality, real room acceptance and response latency require commissioning on the actual hardware. A successful build is not a physical acceptance result.
