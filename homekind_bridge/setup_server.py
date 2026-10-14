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

from bridge import AccessEnded, BridgeError, Connector, ENTITY, atomic_json, request_json, safe_entity


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
            # The phone setup path is always read-only.
            return {"read_only": True, "entity_ids": options.get("entity_ids", []), "pairing_code": options.get("pairing_code", "")}

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
        selected = set(self.options().get("entity_ids", []))
        result = []
        for state in states:
            if not isinstance(state, dict) or not safe_entity(state):
                continue
            attrs = state.get("attributes", {})
            result.append({"entity_id": state["entity_id"], "name": str(attrs.get("friendly_name") or state["entity_id"])[:160], "device_class": str(attrs.get("device_class", ""))[:40], "selected": state["entity_id"] in selected})
        result.sort(key=lambda row: (not row["selected"], row["name"].casefold()))
        return result[:4000]

    def configure(self, data):
        if not isinstance(data, dict) or set(data) - {"pairing_code", "entity_ids"}:
            raise SetupError("Check the pairing code and selected sensors.")
        entities = data.get("entity_ids")
        if not isinstance(entities, list) or not 0 <= len(entities) <= 250 or any(not isinstance(entity, str) or not ENTITY.fullmatch(entity) for entity in entities):
            raise SetupError("Choose between 1 and 250 supported sensors or device states.")
        if len(set(entities)) != len(entities):
            raise SetupError("Choose each sensor once.")
        available = {row["entity_id"] for row in self.entities()} if entities else set()
        if not set(entities) <= available:
            raise SetupError("One of these entities is unavailable or unsupported. Reload the list and choose it again.")
        pairing = data.get("pairing_code", "")
        if not isinstance(pairing, str) or len(pairing) > 256:
            raise SetupError("Paste the pairing code from HomeKind.")
        pairing = pairing.strip()
        if pairing and not re.fullmatch(r"[0-9a-f]{64}", pairing):
            raise SetupError("Paste the complete pairing code from HomeKind.")
        if not entities and not self.paired():
            raise SetupError("Choose at least one sensor to start your home connection.")
        if not pairing and (not self.paired() or self.phase == "pairing_required"):
            raise SetupError("Create a pairing code in HomeKind, then paste it here.")
        with self.lock:
            atomic_json(self.settings, {"pairing_code": pairing, "entity_ids": entities, "read_only": True})
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
            return {"phase": phase, "message": message, "paired": self.paired(), "home_assistant_connected": connected, "last_cloud_at": self.last_cloud_at, "selected_count": len(self.options().get("entity_ids", [])), "read_only": True}

    def run(self):
        while not self.stopped.is_set():
            self.changed.clear()
            try:
                options = self.options()
                if not options.get("entity_ids") or not (options.get("pairing_code") or self.paired()):
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
            files = {"/": ("index.html", "text/html"), "/setup.js": ("setup.js", "text/javascript"), "/setup.css": ("setup.css", "text/css"), "/logo.png": ("logo.png", "image/png"), "/nunito.ttf": ("nunito.ttf", "font/ttf"), "/inter.ttf": ("inter.ttf", "font/ttf")}
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
        if self.path != "/api/configure":
            return self.reply(404, {"error": "Not found."})
        token = self.headers.get("X-HomeKind-Setup", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token) or not hmac.compare_digest(token, self.server.manager.csrf):
            return self.reply(403, {"error": "Reload this setup screen and try again."})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.reply(415, {"error": "Use the setup form."})
        length = self.headers.get("Content-Length", "")
        if not length.isdigit() or not 1 <= int(length) <= 64000:
            return self.reply(413, {"error": "The setup request is too large or incomplete."})
        self.connection.settimeout(10)
        try:
            body = json.loads(self.rfile.read(int(length)))
            return self.reply(200, self.server.manager.configure(body))
        except (json.JSONDecodeError, UnicodeError):
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
