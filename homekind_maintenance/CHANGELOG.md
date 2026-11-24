## 0.2.9 · 24 November 2026 (candidate)

- Resume verified maintenance bookkeeping after an interruption while preserving unresolved outcomes for review. Keep physical updates single-dispatch.
- Recheck the exact available release and household choices before sending a backup, update or reboot request.
- Avoid redundant maintenance requests when saved choices are unchanged and the update window is closed.

## 0.2.7 · 22 November 2026

- Allow up to five minutes for Supervisor's synchronous Core configuration check instead of the ordinary 25-second request timeout.
- A timeout or failed/unverified check still prevents installation. Local logs distinguish a timeout without printing raw configuration or credentials.
- After validation, recheck the saved window, current authority, local opt-out and readiness before dispatch. Preserve the encrypted backup and single-dispatch safeguards.
- Existing failed attempts and their backup/recovery records remain held for review. Upgrading does not clear or replay them. The actual 22 November failure still needs Supervisor logs to confirm its cause.

## 0.2.6 · 21 November 2026

- Audio playback never delays an approved Core/OS update, including Home Assistant audio and unknown sources. Home Assistant audio may pause during the restart.
- Read only running scripts for the local activity check; media players, sources and Home Assistant media configuration are no longer queried.
- Keep running-script checks, exact-version central clearance, household opt-in and saved window, encrypted backup, health, single dispatch and recovery checks.
- Existing pairing, saved choices and private recovery history survive the upgrade. No repeated code, schedule save or release approval is required.

## 0.2.5 · 19 November 2026

- Independent background audio no longer prevents an approved update in its saved window.
- Running Home Assistant scripts, Home Assistant-served audio and unverified media sources remain explicit holds.
- The local maintenance page shows the current check, blocking player/script names and recent reasons. Media URLs, content names and credentials are never included in cloud reports or diagnostic logs.
- Existing pairing, saved schedule, local opt-out, exact-version central clearance, encrypted backup and dispatch/recovery fences are preserved.

## 0.2.4 · 17 November 2026

- Report the result of real local Supervisor health, supported/stable channel, Core API and idle checks; an enabled service alone is not readiness.
- Explain a blocked check using a fixed reason only. Entity names, readings, media content and recovery passwords stay at home.
- Keep saved schedules, local opt-out, exact central release clearance, encrypted backups and single-dispatch recovery fences.
- Upgrading an existing paired home needs no new code or repeated schedule save. Readiness is separate from a completed update.

## 0.2.2 - 11 November 2026

Belmelo name, approved logo and setup wording. Existing app slug, pairing, saved device selections, local opt-out and permissions are retained. No new device access is introduced.

## 0.2.1 · 10 November 2026

- Paste a maintenance code directly into Open Web UI and connect with one explicit local maintenance choice.
- Preserve recovery history; show connection confirmation before scheduling.
- Retain current permissions and update windows; no update starts during pairing.

## 0.2.0 · 9 November 2026

- Scheduled approved Core/OS updates in acknowledged local-time windows, including daylight-saving boundaries, pause and defer.
- Full encrypted backup, fixed update routes, separate OS reboot and restart-safe single dispatch.
- Local Ingress recovery-key page; no cloud export of keys.
- Retained connector updates, settings, pairing and read-only system inventory.
- Requires one-time system-update commissioning; defaults remain off.

