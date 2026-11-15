# Belmelo Voice setup 0.2.1 — 15 November 2026

This one-time installer connects existing ESPHome Assist microphones to Ask Belle inside Belmelo. It preserves microphone firmware, pairing credentials and the existing home connector. Everyday choices live in **Ask Belle → More options → Room microphones**.

1. Install or update **Belmelo Voice setup 0.2.1** from this repository in Home Assistant → Settings → Apps. Open its web UI and choose **Install integration**, then restart Home Assistant once. Existing pairing entries are kept; the setup app can be removed afterwards.
2. In Belmelo, add a microphone, choose its room and **Whole home** or **This room only**. Shared plans and room readings are optional. Confirm these household choices and create a ten-minute pairing code.
3. For a new microphone only, add **Belmelo Voice** under Home Assistant → Devices & services. Paste the code and choose the matching microphone. Check that microphone uses **Belle · your microphone name**. All three engines must be Belmelo Voice, with local command handling off. Existing saved local assistant choices are not overridden on restart.
4. Open that microphone’s **Test** in Belmelo. Its available wake words are reported by the paired satellite. Choose one and wait for it to be applied.
5. Start the microphone test. First say the selected wake word **on its own**. This first wake is consumed by Home Assistant’s satellite test. Once Belmelo confirms it was heard, say the wake word again followed by **“test Belle”**. Confirm you heard Belle introduce herself. A reply-voice preview is not a microphone test.
6. Try a supported home command and check its physical result, for example “Kitchen lights off”, “Close the living room curtains”, “Set Sonos volume to 35 per cent”. An unconfirmed device receipt is described as pending, not successful.

**Whole home** includes supported controls already enabled in Belmelo: lights, plugs, curtains/blinds, fans, heating/cooling and media play/pause/volume. Device capabilities, current household permissions, connector selection, freshness and local opt-outs are checked again at execution. Existing microphones keep their saved light selection until an authorised person explicitly saves new choices. Personal plans and health information remain in each person’s app.

## Hey Belle pilot — 15 November 2026

The trained **Hey Belle 0.1.0** model is an optional household pilot. The regular **Install integration** button keeps its existing behaviour.

For a microphone already paired with Belle:

1. Update **Belmelo Voice setup** to **0.2.1**. Open its web UI, expand **Try the Belle wake-word pilot**, then choose **Add pilot wake words**.
2. Restart Home Assistant once. Your microphone pairing, household choices and active wake word are kept.
3. In **Belmelo → Ask Belle → More options → Room microphones → Test**, select **Hey Belle · pilot** and choose **Use this wake word**. A compatible satellite downloads the fixed model from Home Assistant over the home network. Wait until its actual report shows the phrase active, then complete the spoken microphone test and check a real home command.

The model runs wake detection on the microphone. After a wake, the existing scoped Belle pipeline handles the request. The installer adds only fixed, hash-verified model files under Home Assistant's `custom_wake_words` folder. It does not select a phrase, alter the existing microphone firmware, broaden controls or restart Home Assistant automatically. Existing custom models are preserved; a conflicting local file is left unchanged.

A recent Home Assistant/ESPHome satellite must support runtime custom wake words. The manifest requires ESPHome 2026.10.0 or later. If Hey Belle does not appear in the satellite's reported choices after restarting Home Assistant, keep the current wake word and check firmware compatibility. The installer does not silently flash the microphone or upgrade Core.

### Measured software results

The final model settings were frozen on validation before these test results. Tests use held-out synthetic US-English voices, procedural room responses/noise and separate LibriSpeech recordings. Quiet and noisy variants share the same source utterances.

| Detector check | Quiet | Added procedural noise |
| --- | ---: | ---: |
| Wake word on its own | 153/160 (95.6%) | 149/160 (93.1%) |
| Wake followed immediately by a command; detected within 550 ms of keyword end | 76/80 (95%) | 74/80 (92.5%) |
| Additional acoustic variants of the same command test utterances | 78/80 (97.5%) | 71/80 (88.8%) |

- Ordinary synthetic negative phrases: **0/132** activations.
- Deliberately similar phrases: **4/168** activations; combined hard-negative rate **4/300 (1.33%)**.
- Other Belle wake phrase: **0/320** activations.
- Separate real-speech test exposure: **0 observed activations in 48.5 minutes**. This is a short exposure, not a long-term false-wake guarantee.
- The original stricter gate required at least 95% combined detection and no more than 1% hard-negative acceptance. The final result is **94.2% combined detection and 1.33% hard-negative acceptance**, so that gate **has not passed**. The release remains an explicitly optional experiment.
- **Okay Belle** was also trained and tested. The reviewed comparison achieved 65.6% combined standalone detection and was excluded from the installer.

The follow-on command test checks the detector, not Home Assistant's actual command capture or device action. Physical M5Stack model loading, real household voices, distance, television background, audible replies and end-to-end controls still need checking in the home. No household recordings or personal voice enrolment were used for training.

The final model is 51,072 bytes, input int8 `[1,3,40]`, output uint8 `[1,1]`, 10 ms frontend step, cutoff 0.94 and eight-output sliding window. The manifest reserves a 98,304-byte tensor arena; actual Micro interpreter allocation remains a hardware check. Model SHA-256: `bb677dce2058452efdba2c85efcb47e679b11e0f7a6dba59916bb8121b237f69`.

Detailed machine-readable measurements, source attribution and the model licence are in [wake_words](wake_words). Model availability is read from the actual paired satellite; the cloud accepts only IDs it reports, never an arbitrary URL or a renamed phrase.

The regular installer writes only its own integration folder and backs up the previous version in its own data directory. The separate pilot button adds only the fixed model files described above. It does not restart Home Assistant automatically or store an OpenAI key on the Green. Spoken requests use Belmelo’s existing AI connection. Internet is required; short requests up to thirty seconds are supported. Raw recordings are not persisted by Belmelo.

Software checks use fictional households and satellite events, and the real Home Assistant 2026.11.1 API contract in both supported architecture images. Physical Green installation, microphone audio quality, actual wake detection, audible reply and real-device response latency still require commissioning on the hardware. A passing build is not a physical acceptance result.
