# HomeKind Maintenance Companion 0.1.1

Installer task: install this separate app from the same HomeKind repository as
the HomeKind Home Connector. In HomeKind, open Settings → Home connection health
and updates → Set up maintenance and create a ten-minute maintenance code. Paste
it into this app's `pairing_code` option. Agree the update policy with the resident
and enable `allow_updates` once. Start this companion and enable Start on boot.
Turn off the connector's Home Assistant automatic updates so that they cannot
bypass staged releases. Future feature choices remain in HomeKind.

The companion requires Supervisor's manager role. Its code restricts operations
to `homekind_bridge` in its own repository. It cannot update itself. It creates
an encrypted local partial backup of only that connector before an update and
only restores its own recorded backup. Home Assistant Core, OS, other apps,
folders, arbitrary services and remote command execution are not exposed.

Supervisor installs repository latest; it cannot pin an arbitrary version.
Updates fail closed if repository latest differs from the approved version.
HomeKind enrolment, confirmed choices, devices and personal permissions are not
changed. Backup passwords remain only in the companion's protected data folder.
Do not delete the companion's data if recovery may be needed. Maintain the
installation's separately agreed system backup and recovery route.

Pilot releases require thirty minutes of observed healthy operation before a
stable release can be requested. Stable homes proceed one at a time, with five
healthy minutes between them. Missing observations restart the health period.
Failed or uncertain operations pause their rollout. An uncertain physical
operation is never automatically retried. Recovery requires a manager request
and a matching locally recorded pre-update backup from the last seven days.

Software verification is separate from physical commissioning. Test actual
Green update/reboot/backup restoration, settings preservation, interruption and
post-restore readings before using staged releases across customer homes.

## System observations · 25 October 2026

The companion also reads Core, OS, Supervisor and installed app versions and the latest full system-backup metadata every five minutes. Only version strings and aggregate backup checks leave the home. Backup names, keys, locations, credentials, device values and resident activity are excluded. Encrypted off-device backup evidence is required by the new stable-rollout preflight. A backup that excludes the HA database is not treated as a full recovery point. Observations do not perform a restore or prove physical commissioning.

The separate connector 0.23.0 observes Core changes, available update entities and selected-device/integration health. Existing connector-only updates and recovery retain their fixed endpoints and approval rules. Core/OS, other apps, Zigbee coordinator and device firmware remain centrally reviewed installer tasks.
