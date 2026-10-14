# HomeKind Home Connector 0.3.0

An installable Home Assistant app for the HomeKind pilot. Setup opens inside Home Assistant on your phone: paste a pairing code, tick the sensors you choose and connect. It reuses the existing HomeKind bridge service and runs on your Green.

## Install from the Home Assistant App store

The pilot repository is currently private. A private repository cannot be added using Home Assistant's standard App store repository link. Use the computer-based local install below while keeping the source private. The phone route becomes available after this connector repository is made public; the HomeKind portal, database and household data remain private.

1. Open Home Assistant using an administrator account.
2. Open **Settings → Apps → App store → ⋮ → Repositories** and add the URL of this GitHub repository. Earlier Home Assistant versions call Apps **Add-ons**. HomeKind's **Admin → Devices → Connect Home Assistant** screen will show the repository link once it has been published.
3. Install **HomeKind Home Connector**, enable **Start on boot**, and start it. The first installation builds the pinned container on your Green and needs internet. No pairing code is needed to start the setup screen.
4. Tap **Open Web UI**. Leave this screen ready on your phone.
5. Open your HomeKind household, then **Admin → Devices → Connect Home Assistant → Create pairing code**. Copy the fresh code; it lasts 10 minutes.
6. Return to the HomeKind Web UI inside Home Assistant. Paste the code, tick your intended sensors or device states, then tap **Connect household**. No file copying, terminal command or Home Assistant password is needed for this route.
7. In HomeKind → **Admin → Devices → Add device**, add each discovered sensor and select its room. Exact area-name matches can suggest a room; confirm the actual room before saving.

If GitHub publication has not completed, this package is still suitable for a computer-based local install: copy `homekind_bridge` into Home Assistant's `local_apps` directory, refresh the App store and install it under Local apps. Use **Open Web UI** for the same setup steps. This local-file route requires a computer; it is not the phone installation route.

## Update an existing installation

The `homekind_bridge` app slug and `/data/homekind` credentials directory are unchanged. A local installation and a repository installation have different Home Assistant repository identifiers, so use **Stop connection** in HomeKind before moving between them and pair the replacement once. An update within the same app installation preserves its credentials. Do not run two connectors for the same household.

After updating to 0.3.0, open **Web UI** to manage the sensor selection. That screen's saved selection becomes authoritative; it is kept in the app's private data directory. You can remove every tick and save to pause local sharing, or select sensors again to resume. A refused/expired code or revoked bridge requires a new pairing code; it is not retried in the background. A completed pairing no longer needs its code entered on future starts.

## Household and room setup

Use your own private ChatGPT sign-in for HomeKind. The named pilot testing account does not require an authenticator; other accounts retain their normal checks. Create or open your household with its actual time zone. In **Admin → Rooms & plan**, import the saved pilot if needed. **Rename** keeps a room's identity, model mapping and device assignments. **Delete** removes the room from home views, ends its scoped access and open invitations, and moves devices to Unassigned with controls disabled. The model and historical records are retained.

Choose the intended coverage with the resident before enabling it. History, notifications and routines are separate choices; the connector does not turn them on. Readings describe sensors, without identifying a person or proving that a room is empty.

## Security and verification

- The Web UI is available through Home Assistant Ingress to an administrator. The app accepts Ingress traffic from the Supervisor only and exposes no host port.
- Pairing and bridge credentials remain in the app's private data directory. The cloud stores credential digests. The UI never returns the Supervisor token, bridge token or retained credential file.
- The phone setup path enforces read-only mode. Cameras, microphones, person/location entities and personal health classes are excluded. Discovery sends only the selected supported entity metadata; reading values also require registration in the HomeKind household.
- Home Assistant's `homeassistant_api` capability is broader than a read-only per-entity scope. Code and the explicit local allowlist enforce the restriction. The app requests no Supervisor management, host network, Docker access, privileged mode or extra host mounts.
- The existing bridge retries temporary outages, reconnects with a snapshot, and labels stale readings. It logs connection status and counts, without raw credentials or sensor histories.
- Run the physical checks in `PILOT_ACCEPTANCE.txt`. Automated software checks do not commission your actual Green, sensors, iPhone or iPad.

Detailed behaviour and local computer instructions are in [DOCS.md](homekind_bridge/DOCS.md). The repository has no household floor plan, account data or service-role key.

Bundled Inter and Nunito fonts retain their SIL Open Font License notices in `homekind_bridge/OFL-Inter.txt` and `homekind_bridge/OFL-Nunito.txt`, including inside the built app container.
