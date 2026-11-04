# Software verification

5 November 2026. Prepared source only; no physical endpoint or household
configuration changed. HomeKind hands-free activation remains unfinished.

- Existing connector baseline: `9ab50c913c501ecfd19298bdf5258b9abe3739c0`, 0.25.0.
- Manufacturer YAML matches git blob `be8cb6e19eb88b2c5ae7f3ea933aa8cfe3136a5d`.
- ESPHome 2026.10.2 accepts the complete overlaid configuration, including its
  real external audio stack, sound assets and wake-word models.
- Five setup/readiness regressions pass: private/non-replacing secret creation,
  explicit endpoint identity, incomplete/disabled components, connection URL
  handling and separation of configuration from physical/HomeKind acceptance.
- All **239** existing connector tests pass. The connector runtime is unchanged.
- Local firmware compilation reached ESP-IDF component discovery, where this
  execution environment's process view caused `psutil.NoSuchProcess` before
  CMake could complete. This is not a successful binary build. The dedicated
  CI workflow checks complete compilation on a normal Linux runner; its result
  must be inspected before calling the firmware compiled.
- The known local Green address did not respond from this workspace within the
  bounded connection check. No authenticated Green setup was performed.

No Wi-Fi/device keys, provider tokens, household data or compiled test binary
are committed. CI uses public synthetic values and publishes no installer.

Hardware acceptance remains in `ACCEPTANCE.md`, with every result unverified.
