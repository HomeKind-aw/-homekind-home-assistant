# HomeKind Maintenance Companion 0.2.0

Installer task: install this separate app from the same HomeKind repository as
the HomeKind Home Connector. In HomeKind, open Settings → Home connection health
and updates → Set up maintenance and create a ten-minute maintenance code. Paste
it into this app's `pairing_code` option. Agree the update policy with the resident
and enable `allow_updates` once. Start this companion and enable Start on boot.
Turn off the connector's Home Assistant automatic updates so that they cannot
bypass staged releases. Future feature choices remain in HomeKind.

The companion requires Supervisor's manager role. Connector rollout and recovery
remain restricted to `homekind_bridge` in its own repository. It creates an
encrypted connector-only backup before that update and restores only its own
recorded backup after a current manager request. It cannot update itself or
accept arbitrary services, URLs, app slugs, credentials or scripts from the cloud.

## Household update windows

Version0.2 adds fixed Home Assistant Core and OS update paths. These require the
new local `allow_system_updates` commissioning choice, default off, in addition
to `allow_updates`. Core API access is used only for a fixed local readiness
template; no activity values or speech leave the home. Agree this additional
privilege and the recovery route with the resident during installation.

After commissioning, choose everyday overnight/custom days and times, pause or
defer in HomeKind → Connection health & updates → Updates. Times use the home’s
timezone. The app distinguishes saved choices from the home’s exact durable
acknowledgement. Opting out locally, replacing pairing or re-enrolling invalidates
old pending choices. Unsupported/missing components stay unavailable.

Only one component starts per window. Missed windows are skipped. The executor
checks fresh household authority, local choices, stable supported Supervisor,
local idle state and remaining time before each disruptive operation. It saves
and verifies a full encrypted local backup including the Core database first.
Core gets a configuration check. OS installation and host reboot are separate
steps; the reboot needs a current allowed window and readiness check too.

Waiting home-control requests are cancelled during preparation and are not
replayed after restart. HomeKind retains last readings and keeps other app
features available. Controls require fresh post-maintenance readings. The
executor observes actual versions and at least60seconds of healthy operation.
Uncertain POSTs are never repeated; an observed exact installed/pending version
can recover progress without repeating the update. An unverified backup or
failure pauses this lane for installer review. Backups/passwords remain private
in this app’s data; retain that data for recovery. Full restore is an explicit
installer task, never an automatic overwrite of later resident choices.

Device firmware, other apps, Supervisor’s own automatic updates and manufacturer
services remain under their documented local/vendor update processes. This app
does not invent a safe backup/restore route for unreported device firmware.

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
