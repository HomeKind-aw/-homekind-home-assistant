# Belmelo Maintenance Companion 0.2.6

21 November 2026. Audio playback no longer prevents an approved overnight update, including Home Assistant audio and unknown sources. Home Assistant audio or announcements may pause while the system restarts. Running Home Assistant scripts still hold installation. The local Update check identifies running scripts and retains recent reasons without querying media players or sources. Existing pairing, saved times, local opt-out, central release clearance and recovery fences are preserved.

## One-time setup on your phone

Install or update this app to the latest available version from the existing Belmelo repository, then start it and select **Open Web UI**. This page now includes the pairing form; the Configuration tab is not needed.

In Belmelo open **Settings → Connections → Home Assistant → Set up maintenance**, create a ten-minute code and select **Copy code**. Return to the companion’s **Open Web UI**, paste the code, choose **Allow managed updates and backups**, and select **Connect to Belmelo**. The companion verifies the code, saves the agreed local options in Home Assistant and enables Start on boot. An expired or rejected code does not replace the saved local connection. No system update is dispatched by pairing.

Existing local choices remain in place on an ordinary upgrade; the new form never enables them automatically. Keep the connector’s own Home Assistant automatic updates off when using staged connector rollout.

In Belmelo open **Settings → Connections → Updates & backups**, choose an update window and wait for the home's acknowledgement. The overnight period is 2–5 am in the household's timezone. Choose days, custom hours, pause or defer. Belmelo staff review exact Core/OS versions centrally in **Support → Releases**, first clearing selected pilot homes and later reviewing their actual results before customer clearance. A household schedule does not approve a release. Firmware and other apps retain their installer/vendor process.

An existing paired home only needs **Home Assistant → Settings → Apps → Belmelo Maintenance Companion → Update**. No new pairing code or repeated schedule save is required. The new readiness report appears automatically after the upgraded Companion checks in. A missed earlier window is not replayed during the day.

## Update and recovery

One component is updated at a time. Local and cloud checks require current permission, exact acknowledged choices, stable supported Supervisor, a responding Home Assistant API, no running scripts or pending controls, and enough time before the window closes. Audio playback does not delay an update. Each update creates and verifies an encrypted full system backup including the Home Assistant database. Core configuration is checked before installation. OS installation and its separate reboot each obey the chosen window. Missing a period never causes daytime catch-up.

Every POST is durably recorded before dispatch. A lost response, restart or uncertain result never repeats it. Version and service observations reconcile progress. Controls pause, readings remain last known, queued controls are cancelled, and fresh post-maintenance readings are required before controls return. Failure or uncertain outcomes require review. The local backup journal is retained; never delete Companion data while recovery may be needed.

Open this app's **Web UI** inside Home Assistant to see recorded backup references and reveal a selected recovery password locally. Passwords never leave the home. Keep a downloaded backup and its password safely. Use Home Assistant → Settings → System → Backups for installer-reviewed full restoration; no automated full restore overwrites newer household choices. Core's own Supervisor recovery remains in place.

Existing connector-only staged updates and exact recorded connector-backup recovery remain supported. Pilot/stable rollout gates are unchanged. The independent read-only inventory reporter remains enabled.

Software verification is separate from physical commissioning. An actual Green Core/OS restart, measured reconnection, restored controls and backup restoration must be tested before fleet recovery is marked verified. Publishing does not pair a home or enable updates.

## Scheduled update checks in 0.2.6

Approved Core/OS releases use the saved household window. Audio playback does
not delay updates; running Home Assistant scripts remain holds. Home Assistant
audio may pause during the restart. Core health, current authority,
local opt-out, encrypted backup and single-dispatch recovery checks are unchanged.

Open the existing Web UI and expand **Update check** for the current reason,
the names of running scripts and recent local checks. These details
stay on the Green. Cloud reports contain only fixed reason codes, never media
URLs, content names, recovery keys or household entity values. Upgrading preserves
the pairing and saved schedule; no repeated code or schedule save is needed.
