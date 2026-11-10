# HomeKind Maintenance Companion 0.2.1

9 November 2026. This release adds household-local scheduled, approved Home Assistant Core and OS updates. Existing connector credentials and recovery remain in the same private data folder. It keeps the `homekind_maintenance` slug and manager role; the additional Home Assistant API capability checks local readiness only through fixed routes.

## One-time setup on your phone

Install or update this app to 0.2.1 from the existing HomeKind repository, then start it and select **Open Web UI**. This page now includes the pairing form; the Configuration tab is not needed.

In HomeKind open **Home → Devices → Device health → Set up maintenance**, create a ten-minute code and select **Copy code**. Return to the companion’s **Open Web UI**, paste the code, choose **Allow managed updates and backups**, and select **Connect to HomeKind**. The companion verifies the code, saves the agreed local options in Home Assistant and enables Start on boot. An expired or rejected code does not replace the saved local connection. No system update is dispatched by pairing.

Existing local choices remain in place on an ordinary upgrade; the new form never enables them automatically. Keep the connector’s own Home Assistant automatic updates off when using staged connector rollout.

In HomeKind open **Update times & status**, enable a chosen window and wait for the home's acknowledgement. The suggested overnight period is 2–5 am in the household's timezone. Choose days, custom hours, pause or defer. Review the reported Core/OS release and approve it for that window. Approval is version-specific and cannot inject a URL, arbitrary version, app slug, command or service. Firmware and other apps retain their installer/vendor process.

## Update and recovery

One component is updated at a time. Local and cloud checks require current permission, exact acknowledged choices, stable supported Supervisor, a responding Home Assistant API, no playing media/running scripts or pending controls, and enough time before the window closes. Each update creates and verifies an encrypted full system backup including the Home Assistant database. Core configuration is checked before installation. OS installation and its separate reboot each obey the chosen window. Missing a period never causes daytime catch-up.

Every POST is durably recorded before dispatch. A lost response, restart or uncertain result never repeats it. Version and service observations reconcile progress. Controls pause, readings remain last known, queued controls are cancelled, and fresh post-maintenance readings are required before controls return. Failure or uncertain outcomes require review. The local backup journal is retained; never delete Companion data while recovery may be needed.

Open this app's **Web UI** inside Home Assistant to see recorded backup references and reveal a selected recovery password locally. Passwords never leave the home. Keep a downloaded backup and its password safely. Use Home Assistant → Settings → System → Backups for installer-reviewed full restoration; no automated full restore overwrites newer household choices. Core's own Supervisor recovery remains in place.

Existing connector-only staged updates and exact recorded connector-backup recovery remain supported. Pilot/stable rollout gates are unchanged. The independent read-only inventory reporter remains enabled.

Software verification is separate from physical commissioning. An actual Green Core/OS restart, measured reconnection, restored controls and backup restoration must be tested before fleet recovery is marked verified. Publishing does not pair a home or enable updates.
