# HomeKind Maintenance Companion 0.1.0

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
