# HomeKind connector changes

Routine updates must preserve the `homekind_bridge` slug, `/data/homekind` directory, credentials, selected devices, feature settings, management opt-out and revision.

After one-time local enrolment, use the versioned HomeKind settings protocol for everyday feature choices. Maintain the local device boundary and explicit app consent. Additive feature work must not require another customer-facing Home Assistant checkbox on every upgrade. Use the separate expiring onboarding protocol for supported device discovery and pairing, with verified whole-home management access and explicit HomeKind confirmation. New integration installation and newly required operating-system privileges remain commissioning tasks.

Cloud settings may contain only the defined feature fields. Continue to reject arbitrary services, code, URLs, credentials, entity-selection changes through generic settings, unsupported protocols and replayed revisions. Local edits and revocation take priority. An optional configuration-service failure must leave existing readings and controls running.

Run the meaningful upgrade and permission tests and the full connector suite. Build both supported architectures in CI. Distinguish saved settings, connector acknowledgement and physical device verification.

Device onboarding may only append confirmed, fingerprint-matched entities through its dedicated protocol. Bound pairing to a known local adapter and at most 90 seconds. Persist dispatch before acting, recheck current authorisation, and recover receipts without replaying physical pairing. Never auto-enable controls, live cameras or personal health sharing.
