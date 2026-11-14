# Belmelo Voice setup 0.2.0 — 15 November 2026

This one-time installer connects existing ESPHome Assist microphones to Ask Belle inside Belmelo. It preserves microphone firmware, pairing credentials and the existing home connector. Everyday choices live in **Ask Belle → More options → Room microphones**.

1. Install or update **Belmelo Voice setup 0.2.0** from this repository in Home Assistant → Settings → Apps. Open its web UI and choose **Install integration**, then restart Home Assistant once. Existing pairing entries are kept; the setup app can be removed afterwards.
2. In Belmelo, add a microphone, choose its room and **Whole home** or **This room only**. Shared plans and room readings are optional. Confirm these household choices and create a ten-minute pairing code.
3. For a new microphone only, add **Belmelo Voice** under Home Assistant → Devices & services. Paste the code and choose the matching microphone. Check that microphone uses **Belle · your microphone name**. All three engines must be Belmelo Voice, with local command handling off. Existing saved local assistant choices are not overridden on restart.
4. Open that microphone’s **Test** in Belmelo. Its installed wake words are reported by the paired satellite. Choose one and wait for it to be applied.
5. Start the microphone test. First say the selected wake word **on its own**. This first wake is consumed by Home Assistant’s satellite test. Once Belmelo confirms it was heard, say the wake word again followed by **“test Belle”**. Confirm you heard Belle introduce herself. A reply-voice preview is not a microphone test.
6. Try a supported home command and check its physical result, for example “Kitchen lights off”, “Close the living room curtains”, “Set Sonos volume to 35 per cent”. An unconfirmed device receipt is described as pending, not successful.

**Whole home** includes supported controls already enabled in Belmelo: lights, plugs, curtains/blinds, fans, heating/cooling and media play/pause/volume. Device capabilities, current household permissions, connector selection, freshness and local opt-outs are checked again at execution. Existing microphones keep their saved light selection until an authorised person explicitly saves new choices. Personal plans and health information remain in each person’s app.

Wake-word selection only accepts models actually installed on the paired microphone. **Hey Belle requires a trained wake-word model**; changing a label does not install one. Atom VoiceS3R firmware can report Okay Nabu, Hey Jarvis and Hey Mycroft. No firmware rebuild is needed to switch between installed models. Model availability is read from the actual satellite rather than assumed.

The installer writes only its own integration folder and backs up the previous version in its own data directory. It does not restart Home Assistant automatically or store an OpenAI key on the Green. Spoken requests use Belmelo’s existing AI connection. Internet is required; short requests up to thirty seconds are supported. Raw recordings are not persisted by Belmelo.

Software checks use fictional households and satellite events, and the real Home Assistant 2026.11.1 API contract in both supported architecture images. Physical Green installation, microphone audio quality, actual wake detection, audible reply and real-device response latency still require commissioning on the hardware. A passing build is not a physical acceptance result.
