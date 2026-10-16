#!/usr/bin/env python3
"""HomeKind outbound connector. Python standard library; credentials stay local."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import math
import socket
import threading
import datetime as dt
import getpass
import json
import logging
import os
from pathlib import Path
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

VERSION = "0.6.0"
MAX_PENDING = 250
MAX_OUTBOX_AGE = 86400
CLOUD_URL = "https://saixlksmjzbakopvbsqv.supabase.co/functions/v1/homekind-home-bridge"
ENTITY = re.compile(r"^(sensor|binary_sensor|light|switch|media_player)\.[a-z0-9_]{1,150}$")
SENSOR_CLASSES = {"temperature", "humidity", "illuminance", "battery", "power", "energy", "voltage", "current", "signal_strength", "carbon_dioxide", "pm25", "pm10", "water"}
BINARY_CLASSES = {"motion", "occupancy", "opening", "door", "window", "moisture", "vibration", "connectivity", "battery", "problem"}

class BridgeError(Exception):
    pass

class AccessEnded(BridgeError):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BridgeError("Unexpected redirect; request stopped.")

def request_json(url, token, data=None, timeout=12):
    payload = None if data is None else json.dumps(data, separators=(",", ":")).encode()
    request = urllib.request.Request(url, data=payload, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(NoRedirect(), *( [] if url.startswith(CLOUD_URL + "/") else [urllib.request.ProxyHandler({})] ))
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise BridgeError("Response is too large.")
            return json.loads(raw)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise AccessEnded("Connector access ended. Check pairing or the local Home Assistant account.") from None
        raise BridgeError("HTTP " + str(error.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise BridgeError("Connection unavailable.") from None
    except (json.JSONDecodeError, UnicodeError):
        raise BridgeError("Response could not be read.") from None

def safe_entity(state):
    entity = state.get("entity_id", "")
    if not isinstance(entity, str) or not ENTITY.fullmatch(entity):
        return False
    domain = entity.split(".")[0]
    attrs = state.get("attributes", {})
    if not isinstance(attrs, dict):
        return False
    klass = attrs.get("device_class", "")
    return domain in ("light", "switch", "media_player") or (domain == "sensor" and klass in SENSOR_CLASSES) or (domain == "binary_sensor" and klass in BINARY_CLASSES)

def observation(state):
    attrs = state.get("attributes", {})
    return {"entity_id": state["entity_id"], "state": str(state.get("state", "unknown"))[:100], "unit": str(attrs.get("unit_of_measurement", ""))[:20], "device_class": str(attrs.get("device_class", ""))[:40], "assumed_state": attrs.get("assumed_state") is True, "last_updated": state.get("last_updated"), "last_changed": state.get("last_changed")}

def catalog_entry(state):
    attrs = state.get("attributes", {})
    return {"entity_id": state["entity_id"], "name": str(attrs.get("friendly_name") or state["entity_id"])[:80], "device_class": str(attrs.get("device_class", ""))[:40], "unit": str(attrs.get("unit_of_measurement", ""))[:20]}

def safe_reading(state):
    value = state.get("state", "")
    if not isinstance(value, str) or len(value) > 100:
        return False
    if state["entity_id"].startswith("media_player."):
        return value in ("playing", "paused", "idle", "buffering", "on", "off", "standby", "unknown", "unavailable")
    if state["entity_id"].startswith("sensor."):
        return value in ("unknown", "unavailable") or bool(re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value)) and math.isfinite(float(value))
    return value in ("on", "off", "unknown", "unavailable")

def utc_timestamp(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else 0
    except (ValueError, TypeError, AttributeError):
        return 0

class Connector:
    def __init__(self, options, data_dir, request=request_json, sleeper=time.sleep, clock=time.time):
        self.options, self.request, self.sleep, self.clock = options, request, sleeper, clock
        self.directory = Path(data_dir)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.file = self.directory / "credentials.json"
        self.credentials = json.loads(self.file.read_text()) if self.file.exists() else {"installation_id": str(uuid.uuid4()), "token": secrets.token_hex(32)}
        self.tracked = []
        self.read_only = options.get("read_only", True) is not False
        entities = options.get("entity_ids", [])
        if not isinstance(entities, list) or len(entities) > 250 or any(not isinstance(e, str) or not ENTITY.fullmatch(e) for e in entities):
            raise BridgeError("Choose at most 250 supported entity IDs in the local allowlist.")
        self.allowlist = set(entities)
        self.outbox_file = self.directory / "pending.json"
        self.pending = {}
        self.dropped = 0
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.ready = threading.Event()
        self.halt = threading.Event()
        self.external_stop = threading.Event()
        self.failure = None
        self.stream_connected = False
        self.current = {}
        self.registry = {}
        self.stream = None
        if self.outbox_file.exists():
            if self.outbox_file.stat().st_size > 512000:
                raise BridgeError("The local pending readings file is too large. Stop and check the local installation.")
            saved = json.loads(self.outbox_file.read_text())
            self.pending = {k:v for k,v in saved.items() if k in self.allowlist and isinstance(v, dict) and v.get("entity_id") == k and safe_reading(v) and self.clock()-utc_timestamp(v.get("observed_at")) < MAX_OUTBOX_AGE}

        self.next_sync = 0
        self.backoff = 2
        supervisor = os.environ.get("SUPERVISOR_TOKEN")
        self.supervisor = bool(supervisor)
        if supervisor:
            self.ha_url, self.ha_token = "http://supervisor/core/api", supervisor
        else:
            self.ha_url = str(options.get("ha_url", "http://homeassistant.local:8123")).rstrip("/") + "/api"
            self.ha_token = os.environ.get("HA_ACCESS_TOKEN") or self.credentials.get("ha_token")
        parsed = urllib.parse.urlsplit(self.ha_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise BridgeError("Check the local Home Assistant URL.")

    def save(self):
        atomic_json(self.file, self.credentials)

    def enrol(self):
        if not self.ha_token:
            self.ha_token = getpass.getpass("Home Assistant long-lived access token (kept on this computer): ").strip()
            if not self.ha_token:
                raise BridgeError("A local Home Assistant token is required.")
            self.credentials["ha_token"] = self.ha_token
        pairing = str(self.options.get("pairing_code", "")).strip()
        if self.credentials.get("bridge_id") and (not pairing or self.credentials.get("pairing_used") == pairing):
            return
        if not re.fullmatch(r"[0-9a-f]{64}", pairing):
            raise BridgeError("Create a fresh pairing code in HomeKind.")
        if self.credentials.get("enrol_pending") != pairing:
            self.credentials.update(token=secrets.token_hex(32), installation_id=str(uuid.uuid4()), enrol_pending=pairing)
        self.save()  # Persist the same generated token before the one-time exchange.
        result = self.request(CLOUD_URL + "/enrol", pairing, {"installation_id": self.credentials["installation_id"], "bridge_token": self.credentials["token"]})
        if not isinstance(result, dict) or not isinstance(result.get("bridge_id"), str):
            raise BridgeError("Pairing response could not be read.")
        self.credentials.update(result)
        self.credentials["pairing_used"] = pairing
        self.credentials.pop("enrol_pending", None)
        self.save()

    def cloud(self, operation, data=None):
        return self.request(CLOUD_URL + "/" + operation, self.credentials["token"], {"bridge_id": self.credentials["bridge_id"], **(data or {})})

    def ha(self, path, data=None):
        return self.request(self.ha_url + "/" + path, self.ha_token, data)

    def sync(self):
        try:
            states = self.ha("states")
            if not isinstance(states, list):
                raise BridgeError("Invalid Home Assistant entity list.")
            eligible = [s for s in states if isinstance(s, dict) and safe_entity(s) and s["entity_id"] in self.allowlist]
            catalog = [catalog_entry(s) for s in eligible][:1000]
            selected = {d["entity_id"] for d in self.tracked}
            readings = [observation(s) for s in eligible if s["entity_id"] in selected and safe_reading(s)][:250]
        except BridgeError:
            self.cloud("sync", {"ha_connected": False, "catalog": [], "states": [], "version": VERSION})
            raise
        result = self.cloud("sync", {"ha_connected": True, "catalog": catalog, "states": readings, "version": VERSION})
        self.tracked = result["tracked"]

    def stopping(self):
        return self.halt.is_set() or self.external_stop.is_set()

    def execute(self, claim):
        if self.read_only:
            raise BridgeError("This connector is read-only.")
        if self.stopping():
            return
        # Entity/service arguments are supplied only after current authorization.
        command = None
        sent = False
        result, observed = "failed", {}
        try:
            entity = claim["entity_id"]
            if not ENTITY.fullmatch(entity) or entity.split(".")[0] not in ("light", "switch"):
                raise BridgeError("Unsupported control.")
            before = self.ha("states/" + entity)
            if before.get("state") not in ("on", "off"):
                raise BridgeError("Device is unavailable.")
            if self.stopping():
                return
            command = self.cloud("authorize", {"command_id": claim["id"], "lease_id": claim["lease_id"]})
            if command["entity_id"] != entity:
                raise BridgeError("Control target changed.")
            if not ENTITY.fullmatch(entity) or command["domain"] not in ("light", "switch") or command["service"] != "turn_" + command["desired"] or command["desired"] not in ("on", "off"):
                raise BridgeError("Unsupported control.")
            if entity not in self.allowlist or not any(d["entity_id"] == entity and d["control_enabled"] for d in self.tracked):
                raise BridgeError("Control is not enabled locally.")
            if utc_timestamp(command["expires_at"]) <= self.clock():
                raise BridgeError("Command has expired.")
            if self.stopping():
                return
            if before["state"] == command["desired"]:
                result, observed = "reported", observation(before)
            else:
                sent = True
                self.ha("services/" + command["domain"] + "/" + command["service"], {"entity_id": entity})
                result = "unconfirmed"
                for _ in range(10):
                    if self.stopping():
                        return
                    after = self.ha("states/" + entity)
                    observed = observation(after)
                    if after.get("state") == command["desired"]:
                        changed = after.get("last_updated") != before.get("last_updated") or after.get("context", {}).get("id") != before.get("context", {}).get("id")
                        result = "confirmed" if changed and not observed["assumed_state"] else "reported"
                        self.accept_state(after)
                        break
                    self.sleep(0.2)
        except AccessEnded as error:
            self.failure = error
            self.halt.set()
            self.wake.set()
            raise
        except BridgeError:
            result = "unconfirmed" if sent else "failed"
        # Never retry a service call after a transport failure or lost acknowledgement.
        if command is not None and not self.stopping():
            self.cloud("ack", {"command_id": claim["id"], "lease_id": claim["lease_id"], "status": result, "observation": observed})

    def step(self):
        if self.clock() >= self.next_sync:
            self.sync()
            self.next_sync = self.clock() + 10
        if not self.read_only:
            for claim in self.cloud("poll")["commands"]:
                self.execute(claim)

    def stamp(self):
        return dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc).isoformat()

    def save_pending(self):
        atomic_json(self.outbox_file, self.pending)

    def accept_state(self, state, observed_at=None):
        if not isinstance(state, dict) or state.get("entity_id") not in self.allowlist or not safe_entity(state) or not safe_reading(state):
            return
        if not utc_timestamp(state.get("last_updated")) or not utc_timestamp(state.get("last_changed")):
            return
        entity = state["entity_id"]
        clean = observation(state)
        clean["observed_at"] = observed_at or self.stamp()
        # A check has a deterministic identifier; retries cannot invent another event.
        clean["event_id"] = hashlib.sha256(json.dumps({k:v for k,v in clean.items() if k != "observed_at"}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self.lock:
            previous = self.current.get(entity)
            if previous and utc_timestamp(previous.get("last_updated")) > utc_timestamp(state.get("last_updated")):
                return
            self.current[entity] = {**state, "attributes": dict(state["attributes"])}
            if entity not in {d["entity_id"] for d in self.tracked}:
                return
            if entity not in self.pending and len(self.pending) >= MAX_PENDING:
                self.dropped += 1
                return
            # Latest-state outbox: short-lived transitions aren't represented as history.
            self.pending[entity] = clean
            self.save_pending()
            self.wake.set()

    def stream_sync(self):
        with self.lock:
            tracked = {d["entity_id"] for d in self.tracked} & self.allowlist
            self.pending = {k:v for k,v in self.pending.items() if k in tracked and 0 <= self.clock()-utc_timestamp(v.get("observed_at")) < MAX_OUTBOX_AGE}
            if self.stream_connected:
                # Heartbeats re-observe the current cache only while the socket is healthy.
                for state in list(self.current.values()):
                    if state["entity_id"] in tracked and state["entity_id"] not in self.pending:
                        clean = observation(state)
                        clean["observed_at"] = self.stamp()
                        clean["event_id"] = hashlib.sha256(json.dumps({k:v for k,v in clean.items() if k != "observed_at"}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                        self.pending[state["entity_id"]] = clean
                # Persist one heartbeat batch, rather than fsync once per cached entity.
                self.save_pending()
            connected = self.stream_connected
            catalog = [{**catalog_entry(s), **self.registry.get(s["entity_id"], {})} for s in self.current.values() if safe_entity(s) and s["entity_id"] in self.allowlist]
            batch = list(self.pending.values())[:MAX_PENDING]
            payload = {"ha_connected": connected, "catalog": catalog, "states": batch if connected else [], "version": VERSION,
                       "bridge_info": {"read_only": self.read_only, "stream_connected": connected, "observed_at": self.stamp(), "queue_size": len(self.pending), "queue_dropped": self.dropped}}
        result = self.cloud("sync", payload)
        with self.lock:
            self.tracked = result["tracked"]
            self.ready.set()
            if connected:
                for sent in batch:
                    if self.pending.get(sent["entity_id"], {}).get("event_id") == sent["event_id"]:
                        self.pending.pop(sent["entity_id"], None)
            self.save_pending()

    def command_loop(self, on_status):
        # Commands have their own lane: uploads and one slow device cannot block it.
        delay = 0.5
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="homekind-command") as pool:
            while not self.stopping():
                if not self.ready.wait(0.1):
                    continue
                if self.stopping():
                    return
                try:
                    claims = self.cloud("poll")["commands"]
                    if self.stopping():
                        return
                    jobs = [pool.submit(self.execute, claim) for claim in claims[:3]]
                    for job in jobs:
                        job.result()
                    delay = 0.5
                except AccessEnded as error:
                    self.failure = error
                    self.halt.set()
                    self.wake.set()
                    return
                except BridgeError:
                    logging.warning("HomeKind command connection unavailable; waiting to reconnect.")
                    delay = min(max(2, delay * 2), 30)
                self.halt.wait(delay)

    def run(self, stop_event=None, on_status=lambda event: None):
        self.external_stop = stop_event or threading.Event()
        if self.stopping():
            return
        self.enrol()
        if self.stopping():
            return
        on_status("paired")
        self.stream = HAStream(self)
        self.stream.start()
        worker = None
        if not self.read_only:
            worker = threading.Thread(target=self.command_loop, args=(on_status,), name="homekind-commands", daemon=True)
            worker.start()
        logging.info("HomeKind connector paired. Read-only: %s. Local allowlist: %s entities.", self.read_only, len(self.allowlist))
        next_attempt = 0
        try:
            while not self.stopping():
                self.wake.clear()
                try:
                    with self.lock:
                        due = self.clock() >= next_attempt and (self.clock() >= self.next_sync or bool(self.pending))
                    if due:
                        self.stream_sync()
                        on_status("synced")
                        self.next_sync = self.clock() + 10
                        # Coalesce event bursts and stay below the cloud's sync rate limit.
                        next_attempt = self.clock() + 1
                        self.backoff = 2
                except AccessEnded:
                    raise
                except BridgeError:
                    logging.warning("HomeKind connection unavailable. Retrying in %s seconds.", self.backoff)
                    on_status("retrying")
                    next_attempt = self.clock() + self.backoff
                    self.backoff = min(self.backoff * 2, 30)
                self.wake.wait(min(0.5, max(0.02, next_attempt - self.clock())))
            if self.failure:
                raise self.failure
        finally:
            self.halt.set()
            self.wake.set()
            self.stream.stop()
            if worker:
                worker.join(timeout=30)


def atomic_json(path, data):
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    os.chmod(path, 0o600)


class HAStream:
    """A read-only HA socket; only auth, registry reads and event subscription are sent."""
    def __init__(self, connector, connect=None):
        if connect is None:
            from websockets.sync.client import connect
        self.connect = connect
        self.c = connector
        self.stopped = threading.Event()
        self.thread = None
        self.socket = None

    def start(self):
        self.thread = threading.Thread(target=self.run, name="homekind-ha-readonly", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.socket:
            self.socket.close()
        if self.thread:
            self.thread.join(timeout=5)

    def receive(self, websocket):
        value = json.loads(websocket.recv(timeout=10))
        if not isinstance(value, dict):
            raise BridgeError("Invalid Home Assistant stream response.")
        return value

    def session(self, websocket):
        if self.receive(websocket).get("type") != "auth_required":
            raise BridgeError("Home Assistant authentication was not offered.")
        websocket.send(json.dumps({"type": "auth", "access_token": self.c.ha_token}))
        if self.receive(websocket).get("type") != "auth_ok":
            raise AccessEnded("Home Assistant authentication ended. Check the local account.")
        websocket.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": "state_changed"}))
        answer = self.receive(websocket)
        if answer.get("id") != 1 or answer.get("success") is not True:
            raise BridgeError("Home Assistant state subscription was not accepted.")
        # Subscribe first, then snapshot. Queued events are merged by HA timestamp.
        states = self.c.ha("states")
        if not isinstance(states, list):
            raise BridgeError("Invalid Home Assistant entity list.")
        with self.c.lock:
            self.c.current = {}
            for state in states:
                self.c.accept_state(state)
            self.c.stream_connected = True
            self.c.next_sync = 0
            self.c.wake.set()
        for ident, kind in [(2, "entity"), (3, "device"), (4, "area")]:
            websocket.send(json.dumps({"id": ident, "type": "config/" + kind + "_registry/list"}))
        registries = {}
        while not self.stopped.is_set():
            try:
                message = self.receive(websocket)
            except TimeoutError:
                # websockets ping/pong keeps idle quiet sensors current.
                continue
            if message.get("type") == "result" and message.get("id") in (2, 3, 4):
                if message.get("success") and isinstance(message.get("result"), list):
                    registries[message["id"]] = message["result"]
                    self.registry(registries)
            elif message.get("type") == "event" and message.get("id") == 1:
                data = message.get("event", {}).get("data", {})
                state = data.get("new_state")
                if state is None and data.get("entity_id") in self.c.allowlist:
                    with self.c.lock:
                        previous = self.c.current.get(data["entity_id"])
                        if previous:
                            state = {**previous, "state": "unavailable", "last_updated": self.c.stamp(), "last_changed": self.c.stamp()}
                self.c.accept_state(state)

    def registry(self, registries):
        devices = {d["id"]:d for d in registries.get(3, []) if isinstance(d, dict) and "id" in d}
        areas = {a["area_id"]:a for a in registries.get(4, []) if isinstance(a, dict) and "area_id" in a}
        with self.c.lock:
            for entity in registries.get(2, []):
                if not isinstance(entity, dict) or entity.get("entity_id") not in self.c.allowlist:
                    continue
                device = devices.get(entity.get("device_id"), {})
                area_id = entity.get("area_id") or device.get("area_id")
                self.c.registry[entity["entity_id"]] = {k: str(v)[:200] for k,v in {
                    "ha_unique_id": entity.get("unique_id"), "ha_platform": entity.get("platform"),
                    "ha_device_id": entity.get("device_id"), "ha_area_id": area_id,
                    "ha_area_name": areas.get(area_id, {}).get("name"), "ha_entity_category": entity.get("entity_category")}.items() if v}

    def run(self):
        delay = 2
        while not self.stopped.is_set():
            try:
                url = "ws://supervisor/core/websocket" if self.c.supervisor else self.c.ha_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1) + "/websocket"
                with self.connect(url, proxy=None, compression=None, open_timeout=10, close_timeout=3,
                                  ping_interval=10, ping_timeout=10, max_size=4_000_000, max_queue=32) as websocket:
                    self.socket = websocket
                    self.session(websocket)
                delay = 2
            except Exception:
                # Never log tokens, URLs, registry entries or exception messages.
                logging.warning("Home Assistant stream unavailable. Reconnecting with a fresh snapshot.")
            finally:
                with self.c.lock:
                    self.c.stream_connected = False
                    self.c.next_sync = 0
                    self.c.wake.set()
                self.socket = None
            self.stopped.wait(delay)
            delay = min(delay * 2, 30)


def main():
    parser = argparse.ArgumentParser(description="Connect HomeKind to a local Home Assistant instance.")
    parser.add_argument("--options", default="/data/options.json")
    parser.add_argument("--data", default="/data/homekind")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        options = json.loads(Path(args.options).read_text())
        Connector(options, args.data).run()
    except (BridgeError, OSError, json.JSONDecodeError) as error:
        logging.error("Connector setup did not complete: %s", error)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
