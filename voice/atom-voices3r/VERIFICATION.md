# Software verification

**5 November 2026, after the clean CI build finished at 9:20 am AEDT.**
Prepared firmware/software, not a physically commissioned endpoint. No household
settings, Home Assistant installation, backend schema or real device changed.

## Passed

- Firmware source: **`b6516f961f260844dac2ad3823601e31b12a4872`**. Subsequent
  verification-record and ignore-file updates do not change that firmware.
- **Complete ESPHome 2026.10.2 firmware compilation passed on clean
  `ubuntu-24.04`**, with the real external audio stack, sounds and wake-word models:
  [CI run 41922870917](https://github.com/HomeKind-aw/-homekind-home-assistant/actions/runs/41922870917),
  job `126900148695`. The runner generated factory and OTA images for the
  encrypted-upload configuration.
- The compiler reports **2,140,544 bytes / 3,932,160 bytes flash (54.4%)** and
  **139,952 bytes / 341,760 bytes static RAM (41.0%)**. These are link-time
  measurements, not proof of runtime audio performance or dynamic memory use.
- **Five** setup/readiness regressions pass: private/non-replacing provisioning,
  explicit endpoint identity, incomplete/disabled components, connection URL
  handling and separation of configuration from physical/HomeKind acceptance.
- All **239** existing connector tests pass. Connector and Maintenance Companion
  builds/checks pass on **aarch64 and amd64** for that same source:
  [CI run 41922871176](https://github.com/HomeKind-aw/-homekind-home-assistant/actions/runs/41922871176).
- The unchanged manufacturer YAML matches git blob
  `be8cb6e19eb88b2c5ae7f3ea933aa8cfe3136a5d`; see `upstream.lock.json`.

The initial local build failed in ESP-IDF's parent-process lookup because this
execution environment hides that process from `psutil`. A temporary local
parent-PID fallback allowed a successful local build and was then restored.
**The independent CI build above used the unmodified build tools.** No workaround
is part of the firmware, installer or repository build workflow.

## Still unverified or unfinished

- The known local Green address did not respond from this workspace's bounded
  connection check. No authenticated Green installation was performed.
- A live, metadata-only backend inventory on 5 November confirms `hk_voice`
  is **not installed**, with no corresponding public `hk_voice*` functions.
  No household rows were read and no schema was changed. The matched HomeKind
  hands-free backend, collector, endpoint binding and conversation adapter remain
  software integration work; hardware arrival alone does not complete them.
- No actual microphone, speaker, wake-word, room coverage, latency, physical mute
  persistence, HomeKind action or chosen-recipient delivery test has passed yet.
  Record those separately in `ACCEPTANCE.md`.
- “Hey HomeKind” and sound-of-distress detection are not supplied or activated.

No installation keys, provider tokens, household data or compiled test binary
are committed. Local synthetic credentials were removed; CI publishes no
installer because its binary contains public synthetic values. Generate fresh
private installation values locally when the device arrives.
