# Belmelo Voice setup

**0.3.4 — 26 November 2026.** Prepare Belle now selects Hey Belle automatically on compatible room microphones once connected to their dedicated Belle assistant. The microphone confirms when the phrase is active. A deliberate phrase change takes precedence.

**0.3.3 — 22 November 2026.** Prepare Belle includes the custom wake phrase. Newly added microphones pick Hey Belle when the hardware reports it; existing wake settings are preserved.

**0.3.2.** An interrupted spoken request now releases the next request immediately after the interrupted pipeline stops. If two requests hear the same words, each keeps its own transcription turn. The guided setup protocol still reports 0.3.0 for compatibility. Pairing shows ESPHome Assist microphones only. Incidental conversation reported by the Belmelo gateway is suppressed before spoken playback.

This one-time installer prepares existing ESPHome Assist microphones for Ask Belle. Microphone firmware, pairing and existing home permissions are kept. Everyday choices stay in **Ask Belle → More options → Set up room microphones**.

1. Open **Belmelo Voice setup**, choose **Prepare Belle**, then restart Home Assistant once. Preparation includes the Hey Belle pilot model.
2. Return to **Room microphones** in Belmelo. The guided screen resumes with the microphone’s actual connection report. A new microphone needs one expiring pairing code in Home Assistant; an already paired microphone skips that step. Choose **Connect to Belle** if its assistant needs connecting. This selects only that microphone’s own dedicated Belle pipeline, after an explicit authorised request that expires after 90 seconds. Ordinary restarts keep local assistant choices.
3. Once the microphone reports **Hey Belle** as active, choose **Start spoken check**, say **“Hey Belle, test Belle”**, then confirm you heard Belle introduce herself. A fresh transcription, conversation and spoken reply through the paired pipeline must precede that confirmation. The check does not claim an independent intercepted wake event. The older two-utterance test remains available to older app versions.

A request for a changed wake phrase stays pending until the microphone reports it active. If Home Assistant or the connection restarts, return to this same screen; pairing, access and current test progress come from the server. Pairing codes are never saved in browser storage. A reply-voice preview cannot pass the spoken check.

Afterwards, try an enabled home command and check its physical result. Software deployment does not verify a real microphone’s sound or a home control’s physical result.

When pairing a new microphone, choose **Whole home** or **This room only**. Shared plans and room readings are optional. Check that the microphone uses **Belle · your microphone name**. All three engines must be Belmelo Voice, with local command handling off.

**Whole home** includes supported controls already enabled in Belmelo: lights, plugs, curtains/blinds, fans, heating/cooling and media play/pause/volume. Device capabilities, current household permissions, connector selection, freshness and local opt-outs are checked again at execution. Existing microphones keep their saved light selection until an authorised person explicitly saves new choices. Personal plans and health information remain in each person’s app.

## Hey Belle pilot — 15 November 2026

The trained **Hey Belle 0.1.0** model is a household pilot. **Prepare Belle** authorises Hey Belle for connected Belle microphones that report support. Existing and newly paired microphones each get their own confirmation. A microphone that has already completed this setup keeps later phrase choices.

The model runs wake detection on the microphone. After a wake, the existing scoped Belle pipeline handles the request. The installer adds fixed, hash-verified model files under Home Assistant's `custom_wake_words` folder. After a manual restart, the integration requests the phrase from compatible microphones connected to Belle. Existing custom models are preserved and conflicting local files are left unchanged.

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

The installer writes its own integration folder and the fixed model files described above, and backs up the previous version in its own data directory. Spoken requests use Belmelo’s existing AI connection. Internet is required and short requests up to thirty seconds are supported. Raw recordings are not persisted by Belmelo.

Software checks use fictional households and satellite events, and the real Home Assistant 2026.11.1 API contract in both supported architecture images. Physical Green installation, microphone audio quality, actual wake detection, audible reply and real-device response latency still require commissioning on the hardware. A passing build is not a physical acceptance result.
