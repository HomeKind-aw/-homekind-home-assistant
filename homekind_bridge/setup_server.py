#!/usr/bin/env python3
"""Home Assistant Ingress setup. No public port, service token or cloud key UI."""
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bridge import AccessEnded, BridgeError, Connector, ENTITY, VERSION, CLOUD_URL, atomic_json, request_json, safe_entity, catalog_entry, button_candidates
from health_import import HealthImports, HealthImportError
import managed_settings


class SetupError(Exception):
    pass


class SetupManager:
    def __init__(self, data_dir="/data/homekind", options_file="/data/options.json", request=request_json):
        self.directory = Path(data_dir)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.settings = self.directory / "setup.json"
        self.credentials = self.directory / "credentials.json"
        self.options_file = Path(options_file)
        self.request = request
        self.csrf = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.changed = threading.Event()
        self.stopped = threading.Event()
        self.connector = None
        self.message = "Choose the sensors to share, then connect your household."
        self.phase = "setup"
        self.last_cloud_at = None
        self.health = None
        try:
            self.health = HealthImports(self.directory, self.publish_health, atomic_json)
            threading.Thread(target=self.health.run, args=(self.stopped,), name="homekind-local-health", daemon=True).start()
        except (HealthImportError, OSError, ValueError, RecursionError):
            pass  # Optional health-store failure cannot stop ordinary home sensors.

    def publish_health(self, entity, state):
        if not re.fullmatch(r"sensor\.homekind_health_[0-9a-f]{12}_(steps|sleep|resting_hr)", entity):
            raise SetupError("Choose a HomeKind-owned health source.")
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            raise SetupError("Open this app inside Home Assistant.")
        self.request("http://supervisor/core/api/states/" + entity, token, state, timeout=3)

    def health_action(self, action, data):
        if not self.health or not isinstance(data, dict):
            raise SetupError("The local health import is unavailable. Check the app locally.")
        actions = {"inspect": self.health.inspect, "preview": self.health.preview, "commit": self.health.commit, "remove": self.health.remove}
        if action not in actions:
            raise SetupError("Choose a supported health import action.")
        try:
            return actions[action](data)
        except HealthImportError as error:
            raise SetupError(str(error)) from None

    def options(self):
        with self.lock:
            path = self.settings if self.settings.exists() else self.options_file
            if not path.exists():
                return {"read_only": True, "entity_ids": [], "pairing_code": ""}
            if path.stat().st_size > 64000:
                raise SetupError("The saved settings need to be checked locally.")
            options = json.loads(path.read_text())
            if not isinstance(options, dict):
                raise SetupError("The saved settings need to be checked locally.")
            try:
                return managed_settings.normalise(options)
            except managed_settings.SettingsError as error:
                raise SetupError(str(error)) from None

    def paired(self):
        if not self.credentials.exists():
            return False
        if self.credentials.stat().st_size > 64000:
            return False
        return bool(json.loads(self.credentials.read_text()).get("bridge_id"))

    def entities(self):
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            raise SetupError("Open this app inside Home Assistant on your Green.")
        try:
            states = self.request("http://supervisor/core/api/states", token)
        except BridgeError:
            raise SetupError("Home Assistant is not ready. Wait a moment, then reload the sensor list.") from None
        if not isinstance(states, list) or len(states) > 20000:
            raise SetupError("The sensor list could not be read. Try reloading it.")
        states = states + button_candidates(states, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        selected = set(self.options().get("entity_ids", []))
        result = []
        for state in states:
            if not isinstance(state, dict) or not safe_entity(state):
                continue
            attrs = state.get("attributes", {})
            result.append({**catalog_entry(state), "selected": state["entity_id"] in selected})
        result.sort(key=lambda row: (not row["selected"], row["name"].casefold()))
        return result[:4000]

    def configure(self, data):
        if not isinstance(data, dict) or set(data) - {"pairing_code", "entity_ids", "read_only", "live_camera_entity_ids", "media_control_entity_ids", "managed_settings"}:
            raise SetupError("Check the pairing code and selected sensors.")
        if "read_only" in data and not isinstance(data["read_only"], bool):
            raise SetupError("Choose whether device controls are allowed.")
        if "managed_settings" in data and type(data["managed_settings"]) is not bool:
            raise SetupError("Choose whether to manage features in HomeKind.")
        previous = self.options()
        read_only = data.get("read_only", self.options()["read_only"])
        entities = data.get("entity_ids")
        if not isinstance(entities, list) or not 0 <= len(entities) <= 250 or any(not isinstance(entity, str) or not ENTITY.fullmatch(entity) for entity in entities):
            raise SetupError("Choose between 1 and 250 supported sensors or device states.")
        if len(set(entities)) != len(entities):
            raise SetupError("Choose each sensor once.")
        available = {row["entity_id"] for row in self.entities()} if entities else set()
        if not set(entities) <= available:
            raise SetupError("One of these entities is unavailable or unsupported. Reload the list and choose it again.")
        live = data.get("live_camera_entity_ids", [e for e in self.options().get("live_camera_entity_ids", []) if e in entities])
        if not isinstance(live, list) or len(live) > 20 or any(not isinstance(e, str) or not re.fullmatch(r"camera\.[a-z0-9_]{1,150}", e) or e not in entities for e in live) or len(set(live)) != len(live):
            raise SetupError("Choose live cameras from the selected camera readings.")
        pairing = data.get("pairing_code", "")
        media = data.get("media_control_entity_ids", [e for e in self.options().get("media_control_entity_ids", []) if e in entities])
        if not isinstance(media, list) or len(media) > 20 or any(not isinstance(e, str) or not re.fullmatch(r"media_player\.[a-z0-9_]{1,150}", e) or e not in entities for e in media) or len(set(media)) != len(media):
            raise SetupError("Choose media controls from your selected media players.")
        if not isinstance(pairing, str) or len(pairing) > 256:
            raise SetupError("Paste the pairing code from HomeKind.")
        pairing = pairing.strip()
        if pairing and not re.fullmatch(r"[0-9a-f]{64}", pairing):
            raise SetupError("Paste the complete pairing code from HomeKind.")
        if not entities and not self.paired() and not data.get("managed_settings", previous.get("managed_settings", False)):
            raise SetupError("Choose at least one sensor to start your home connection.")
        if not pairing and (not self.paired() or self.phase == "pairing_required"):
            raise SetupError("Create a pairing code in HomeKind, then paste it here.")
        with self.lock:
            if self.options() != previous:
                raise SetupError("Settings changed. Reload this screen before saving.")
            saved = {**previous, "pairing_code": pairing, "entity_ids": entities, "read_only": read_only, "live_camera_entity_ids": live, "media_control_entity_ids": media,
                     "managed_settings": data.get("managed_settings", previous.get("managed_settings", False))}
            local_change = any(saved.get(key) != previous.get(key) for key in ("managed_settings", "entity_ids", "read_only", "live_camera_entity_ids", "media_control_entity_ids"))
            if local_change:
                saved.update(management_id=secrets.token_hex(16) if saved["managed_settings"] else "", applied_revision=0)
            saved = managed_settings.normalise(saved)
            atomic_json(self.settings, saved)
            self.phase = "starting"
            self.message = "Settings saved. Connecting your selected sensors…"
            self.changed.set()
        return {"ok": True}

    def status(self):
        with self.lock:
            c = self.connector
            connected = bool(c and c.stream_connected)
            phase = "waiting" if self.phase == "connected" and not connected else self.phase
            message = "Waiting for a fresh Home Assistant connection." if phase != self.phase else self.message
            return {"phase": phase, "message": message, "paired": self.paired(), "button_inputs": list(c.button_status.values()) if c else [], "home_assistant_connected": connected, "last_cloud_at": self.last_cloud_at, "selected_count": len(self.options().get("entity_ids", [])), "read_only": self.options()["read_only"], "live_camera_entity_ids": self.options().get("live_camera_entity_ids", []), "media_control_entity_ids": self.options().get("media_control_entity_ids", []), "managed_settings": self.options().get("managed_settings", False), "applied_revision": self.options().get("applied_revision", 0)}

    def configuration_once(self):
        with self.lock:
            if not self.paired():
                return
            options = self.options()
            credentials = json.loads(self.credentials.read_text())
        payload = {"bridge_id": credentials["bridge_id"], "protocol": managed_settings.PROTOCOL, "version": VERSION,
                   "management_id": options.get("management_id", ""), "applied_revision": options.get("applied_revision", 0),
                   "selected_entities": options["entity_ids"], "config": {key: options.get(key, [] if key != "read_only" else True) for key in managed_settings.FIELDS}}
        # Separate personal-location protocol, piggybacked on the existing poll.
        # Its failure must not interrupt ordinary settings, readings or controls.
        try:
            if not hasattr(self, "location_sharing"):
                from location_sharing import LocationSharing
                self.location_sharing = LocationSharing(self)
            payload["location"] = self.location_sharing.prepare(options)
        except Exception:
            if hasattr(self, "location_sharing"):
                self.location_sharing.clear()
        result = self.request(CLOUD_URL + "/configuration", credentials["token"], payload, timeout=5)
        if hasattr(self, "location_sharing"):
            self.location_sharing.accept(result.get("location") if isinstance(result, dict) else None, options)
        with self.lock:
            # A concurrent local edit, pause or replacement always takes priority.
            if options != self.options() or credentials != json.loads(self.credentials.read_text()):
                return
            saved = managed_settings.apply(options, result.get("desired") if isinstance(result, dict) else None)
            if saved is not None:
                atomic_json(self.settings, saved)
                self.changed.set()

    def configuration_loop(self):
        while not self.stopped.is_set():
            try:
                self.configuration_once()
            except (BridgeError, SetupError, OSError, ValueError, KeyError, RecursionError):
                # Settings failures cannot interrupt readings or device controls.
                pass
            self.stopped.wait(15)

    def run(self):
        while not self.stopped.is_set():
            self.changed.clear()
            try:
                options = self.options()
                if (not options.get("entity_ids") and not options.get("managed_settings")) or not (options.get("pairing_code") or self.paired()):
                    if self.paired() and not options.get("entity_ids"):
                        with self.lock:
                            self.phase = "paused"
                            self.message = "Live sharing is paused. Choose sensors to resume."
                    self.changed.wait(2)
                    continue
                c = Connector(options, self.directory, request=self.request)
                with self.lock:
                    self.connector = c
                    self.phase = "connecting"
                    self.message = "Opening your home connection…"

                def status_update(event):
                    with self.lock:
                        if self.changed.is_set():
                            return
                        if event == "paired":
                            self.phase = "connecting"
                            self.message = "Paired. Checking Home Assistant and sending selected readings…"
                            # Credentials persist; discard the one-time pairing secret.
                            saved = self.options()
                            if saved["pairing_code"] == options["pairing_code"]:
                                saved["pairing_code"] = ""
                                atomic_json(self.settings, saved)
                        elif event == "synced":
                            self.last_cloud_at = time.time()
                            self.phase = "connected" if c.stream_connected else "waiting"
                            self.message = ("Selected readings are reaching HomeKind." if c.tracked else "Connected. Add your selected sensors to rooms in HomeKind → Admin → Devices.") if c.stream_connected else "Paired. Waiting for a fresh Home Assistant connection."
                        elif event == "retrying":
                            self.phase = "waiting"
                            self.message = "Waiting for the home or internet connection. Retrying automatically."

                c.run(stop_event=self.changed, on_status=status_update)
            except AccessEnded:
                with self.lock:
                    self.phase = "pairing_required"
                    self.message = "The pairing code expired or access ended. Create a fresh code in HomeKind and connect again."
                # No background retry of a refused one-time code.
                self.changed.wait()
            except (BridgeError, SetupError, OSError, ValueError):
                with self.lock:
                    self.phase = "waiting"
                    self.message = "The connection is not ready. Check the pairing code and internet connection."
                self.changed.wait(10)
            finally:
                with self.lock:
                    self.connector = None


class SetupServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, manager, *, ingress_ip="172.30.32.2"):
        self.manager = manager
        self.ingress_ip = ingress_ip
        super().__init__(address, SetupHandler)


class SetupHandler(BaseHTTPRequestHandler):
    # The Supervisor authenticates the person. No X-Forwarded-For trust or CORS.
    server_version = "HomeKind"

    def log_message(self, *_):
        pass

    def permitted(self):
        return self.client_address[0] == self.server.ingress_ip

    def reply(self, status, body, content_type="application/json"):
        if content_type == "application/json":
            body = json.dumps(body, separators=(",", ":")).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.permitted():
            return self.reply(403, {"error": "Open through Home Assistant."})
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/status":
                return self.reply(200, self.server.manager.status())
            if path == "/api/entities":
                return self.reply(200, {"entities": self.server.manager.entities()})
            if path == "/api/setup":
                return self.reply(200, {"csrf": self.server.manager.csrf})
            if path == "/api/health/status":
                health = self.server.manager.health
                return self.reply(200, health.status() if health else {"profiles": [], "automatic_sync": False, "message": "Local health import unavailable. Check the app locally."})
            files = {"/": ("index.html", "text/html"), "/setup.js": ("setup.js", "text/javascript"), "/health.js": ("health.js", "text/javascript"), "/setup.css": ("setup.css", "text/css"), "/logo.png": ("logo.png", "image/png"), "/nunito.ttf": ("nunito.ttf", "font/ttf"), "/inter.ttf": ("inter.ttf", "font/ttf")}
            if path in files:
                name, kind = files[path]
                return self.reply(200, (Path(__file__).parent / name).read_bytes(), kind)
            return self.reply(404, {"error": "Not found."})
        except SetupError as error:
            return self.reply(503, {"error": str(error)})
        except (OSError, ValueError):
            return self.reply(503, {"error": "Setup is temporarily unavailable. Check the app log or restart the app."})

    def do_POST(self):
        if not self.permitted():
            return self.reply(403, {"error": "Open through Home Assistant."})
        health_action = self.path.removeprefix("/api/health/") if self.path.startswith("/api/health/") else None
        if self.path != "/api/configure" and health_action not in ("inspect", "preview", "commit", "remove"):
            return self.reply(404, {"error": "Not found."})
        token = self.headers.get("X-HomeKind-Setup", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token) or not hmac.compare_digest(token, self.server.manager.csrf):
            return self.reply(403, {"error": "Reload this setup screen and try again."})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.reply(415, {"error": "Use the setup form."})
        length = self.headers.get("Content-Length", "")
        if not length.isdigit() or not 1 <= int(length) <= (262144 if health_action else 64000):
            return self.reply(413, {"error": "The setup request is too large or incomplete."})
        self.connection.settimeout(10)
        try:
            body = json.loads(self.rfile.read(int(length)))
            return self.reply(200, self.server.manager.health_action(health_action, body) if health_action else self.server.manager.configure(body))
        except (json.JSONDecodeError, UnicodeError, RecursionError):
            return self.reply(400, {"error": "Check the setup form and try again."})
        except SetupError as error:
            return self.reply(400, {"error": str(error)})
        except (OSError, ValueError):
            return self.reply(503, {"error": "Settings could not be saved. Try again."})


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if not os.environ.get("SUPERVISOR_TOKEN"):
        logging.error("Use this setup app inside Home Assistant. For a separate local computer, run bridge.py.")
        return 1
    manager = SetupManager()
    worker = threading.Thread(target=manager.run, daemon=True)
    worker.start()
    threading.Thread(target=manager.configuration_loop, name="homekind-settings", daemon=True).start()
    try:
        from device_onboarding import DeviceOnboarding
        onboarding = DeviceOnboarding(manager)
        threading.Thread(target=onboarding.run, name="homekind-onboarding", daemon=True).start()
    except Exception:
        logging.warning("Device setup could not start; existing home readings continue.")
    logging.info("HomeKind setup is ready. Open Web UI in Home Assistant.")
    with SetupServer(("0.0.0.0", 8099), manager) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            manager.stopped.set()
            manager.changed.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
