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

VERSION = "0.12.0"
MAX_PENDING = 250
MAX_OUTBOX_AGE = 86400
CLOUD_URL = "https://saixlksmjzbakopvbsqv.supabase.co/functions/v1/homekind-home-bridge"
ENTITY = re.compile(r"^(sensor|binary_sensor|light|switch|media_player|camera|event|weather|vacuum)\.[a-z0-9_]{1,150}$")
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

HEALTH_UNITS = {
    "health_steps": {"steps", "step", ""}, "health_sleep": {"min", "minutes", "h", "hours", "s"},
    "health_resting_hr": {"bpm", "beats/min"}, "health_heart_rate": {"bpm", "beats/min"},
    "health_oxygen": {"%"}, "health_weight": {"kg", "g", "lb", "lbs"},
    "health_body_temperature": {"°C", "°F", "C", "F"},
    "health_systolic": {"mmHg"}, "health_diastolic": {"mmHg"},
    "health_glucose": {"mg/dL", "mmol/L"},
}
WEATHER_STATES = {"clear-night", "cloudy", "fog", "hail", "lightning", "lightning-rainy", "partlycloudy", "pouring", "rainy", "snowy", "snowy-rainy", "sunny", "windy", "windy-variant", "exceptional", "unknown", "unavailable"}
RING_TYPES = {"ring", "pressed", "single_press"}
BUTTON_ACTIONS = {"single", "double", "triple", "hold", "release", "press"}
BUTTON_ALIASES = {"single_press": "single", "double_press": "double", "triple_press": "triple", "long_press": "hold", "long_press_start": "hold", "long_press_end": "release", "long_release": "release"}

def button_action(value):
    if not isinstance(value, str):
        return None
    return value if value in BUTTON_ACTIONS else BUTTON_ALIASES.get(value)

def mqtt_button_bindings(info, triggers, device_id):
    """Use the selected device's HA discovery only; never guess or wildcard MQTT topics."""
    if not isinstance(info, dict) or not isinstance(triggers, list):
        return []
    known = {(t.get("type"), t.get("subtype")) for t in triggers[:128]
             if isinstance(t, dict) and t.get("device_id") == device_id and t.get("domain") == "mqtt"
             and t.get("platform", t.get("trigger")) == "device" and t.get("type") == "action"
             and isinstance(t.get("subtype"), str) and button_action(t.get("subtype"))}
    rows = info.get("triggers")
    if not isinstance(rows, list):
        return []
    bindings = []
    for row in rows[:128]:
        discovery = row.get("discovery_data") if isinstance(row, dict) else None
        cfg = discovery.get("payload") if isinstance(discovery, dict) else None
        if not isinstance(cfg, dict) or not isinstance(cfg.get("type"), str) or not isinstance(cfg.get("subtype"), str) or (cfg.get("type"), cfg.get("subtype")) not in known:
            continue
        topic, payload, template = cfg.get("topic"), cfg.get("payload"), cfg.get("value_template")
        action = button_action(cfg.get("subtype"))
        if not isinstance(topic, str) or not 1 <= len(topic) <= 512 or any(c in topic for c in ("+", "#", "\x00", "{{", "}}")):
            continue
        if not isinstance(payload, str) or len(payload) > 100 or button_action(payload) != action:
            continue
        # Only the simple standard action field is interpreted. No templates are executed.
        json_action = isinstance(template, str) and re.sub(r"\s+", "", template) == "{{value_json.action}}"
        if template not in (None, "") and not json_action:
            continue
        bindings.append({"topic": topic, "payload": payload, "action": action, "json_action": json_action})
    return bindings
VACUUM_STATES = {"cleaning", "docked", "returning", "paused", "idle", "error", "unknown", "unavailable"}

def reading_class(state):
    """Recognise personal readings explicitly; ordinary room readings stay ordinary."""
    attrs = state.get("attributes", {})
    if str(state.get("entity_id", "")).startswith("vacuum."):
        return "vacuum"
    klass = str(attrs.get("device_class", ""))
    entity = str(state.get("entity_id", ""))
    words = re.sub(r"[_.-]+", " ", entity + " " + str(attrs.get("friendly_name", ""))).lower()
    unit = str(attrs.get("unit_of_measurement", ""))
    if entity.startswith(("binary_sensor.", "event.")) and (klass == "doorbell" or re.search(r"\bdoor ?bell\b", words)):
        return "doorbell"
    if not entity.startswith("sensor."):
        return klass
    if klass in HEALTH_UNITS:
        return klass if unit in HEALTH_UNITS[klass] else "unsupported_health"
    metric = {"heart_rate":"health_heart_rate", "resting_heart_rate":"health_resting_hr", "steps":"health_steps", "sleep_duration":"health_sleep", "blood_oxygen":"health_oxygen", "blood_glucose_concentration":"health_glucose"}.get(klass)
    if metric:
        return metric if unit in HEALTH_UNITS[metric] else "unsupported_health"
    metric = None
    if re.search(r"\b(resting heart rate|resting hr|resting pulse)\b", words): metric = "health_resting_hr"
    elif re.search(r"\b(heart rate|pulse rate)\b", words): metric = "health_heart_rate"
    elif re.search(r"\b(steps|step count)\b", words): metric = "health_steps"
    elif re.search(r"\b(sleep duration|sleep time|time asleep|total sleep|sleep)\b", words): metric = "health_sleep"
    elif re.search(r"\b(spo2|blood oxygen|oxygen saturation)\b", words): metric = "health_oxygen"
    elif re.search(r"\b(systolic)\b", words): metric = "health_systolic"
    elif re.search(r"\b(diastolic)\b", words): metric = "health_diastolic"
    elif klass == "blood_glucose_concentration" or re.search(r"\b(blood glucose|blood sugar|glucose)\b", words): metric = "health_glucose"
    elif re.search(r"\b(body temperature|skin temperature)\b", words): metric = "health_body_temperature"
    elif re.search(r"\b(body weight|weight)\b", words) and unit in HEALTH_UNITS["health_weight"]: metric = "health_weight"
    return (metric if unit in HEALTH_UNITS[metric] else "unsupported_health") if metric else klass

def safe_entity(state):
    entity = state.get("entity_id", "")
    if not isinstance(entity, str) or not ENTITY.fullmatch(entity) or not isinstance(state.get("attributes", {}), dict):
        return False
    domain = entity.split(".")[0]
    klass = reading_class(state)
    return domain in ("light", "switch", "media_player", "camera", "weather", "vacuum") or (domain == "event" and klass in ("doorbell", "button")) or (domain == "sensor" and (klass in SENSOR_CLASSES or klass in HEALTH_UNITS)) or (domain == "binary_sensor" and (klass in BINARY_CLASSES or klass == "doorbell"))

def observation(state):
    attrs = state.get("attributes", {})
    result = {"entity_id": state["entity_id"], "state": str(state.get("state", "unknown"))[:100], "unit": str(attrs.get("unit_of_measurement", ""))[:20], "device_class": reading_class(state)[:40], "assumed_state": attrs.get("assumed_state") is True, "last_updated": state.get("last_updated"), "last_changed": state.get("last_changed")}
    if state["entity_id"].startswith("weather."):
        result.update(unit="", device_class="weather")
        for key in ("temperature", "humidity", "wind_speed"):
            value = attrs.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                result[key] = value
        for key, allowed in (("temperature_unit", {"°C", "°F"}), ("wind_speed_unit", {"km/h", "m/s", "mph", "kn"})):
            if attrs.get(key) in allowed: result[key] = attrs[key]
    if state["entity_id"].startswith("camera."):
        result.update(unit="", device_class="")
    if state["entity_id"].startswith("event."):
        result["unit"] = ""
        result["event_type"] = (button_action(attrs.get("event_type")) or "") if reading_class(state) == "button" else "ring" if attrs.get("event_type") in RING_TYPES else ""
    if result["device_class"].startswith("health_") and attrs.get("homekind_health_source") in ("garmin_export", "health_file"):
        day = attrs.get("homekind_health_date")
        try:
            if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                raise ValueError()
            dt.date.fromisoformat(day)
            zone = attrs.get("homekind_health_timezone")
            if not isinstance(zone, str) or not re.fullmatch(r"[A-Za-z0-9_+.-]+(?:/[A-Za-z0-9_+.-]+){0,2}", zone) or len(zone) > 80:
                raise ValueError()
            result.update(import_method="file", source=attrs["homekind_health_source"], source_date=day, source_timezone=zone)
        except ValueError:
            result["state"] = "unavailable"
    return result

def catalog_entry(state):
    attrs = state.get("attributes", {})
    return {"entity_id": state["entity_id"], "name": str(attrs.get("friendly_name") or state["entity_id"])[:80], "device_class": "" if state["entity_id"].startswith("camera.") else reading_class(state)[:40], "unit": "" if state["entity_id"].startswith(("camera.", "event.")) else str(attrs.get("unit_of_measurement", ""))[:20]}

def button_candidates(states, stamp):
    """Local administrators can select a physical button action separately from its battery."""
    rows = []
    for source in states:
        if not isinstance(source, dict):
            continue
        entity = source.get("entity_id", "")
        attrs = source.get("attributes", {})
        if not isinstance(attrs, dict):
            continue
        name = str(attrs.get("friendly_name", entity))
        if not isinstance(entity, str) or not ENTITY.fullmatch(entity) or not entity.startswith("sensor.") or not entity.endswith("_battery") or not re.search(r"\b(button|remote|switch)\b", re.sub(r"[_]+", " ", name + " " + entity), re.I):
            continue
        rows.append({"entity_id": "event.homekind_" + hashlib.sha256(entity.encode()).hexdigest()[:16], "state": "unknown", "last_updated": stamp, "last_changed": stamp,
                     "attributes": {"friendly_name": re.sub(r"\s+battery$", "", name, flags=re.I) + " press", "device_class": "button", "source_entity_id": entity, "event_type": ""}})
    return rows

def safe_reading(state):
    value = state.get("state", "")
    if not isinstance(value, str) or len(value) > 100:
        return False
    if state["entity_id"].startswith("vacuum."):
        return value in VACUUM_STATES
    if state["entity_id"].startswith("weather."):
        return value in WEATHER_STATES
    if state["entity_id"].startswith("camera."):
        return value in ("idle", "streaming", "recording", "on", "off", "unknown", "unavailable")
    if state["entity_id"].startswith("event."):
        return value in ("unknown", "unavailable") or utc_timestamp(value) > 0
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

def pending_key(reading):
    entity = reading.get("entity_id", "")
    event_id = reading.get("event_id", "")
    if entity.startswith("event.") and reading.get("device_class") == "button" and reading.get("event_type") in BUTTON_ACTIONS and isinstance(event_id, str) and re.fullmatch(r"[0-9a-f]{64}", event_id):
        return entity + "|" + event_id
    return entity

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
        live = options.get("live_camera_entity_ids", [])
        if not isinstance(live, list) or len(live) > 20 or any(not isinstance(e, str) or not re.fullmatch(r"camera\.[a-z0-9_]{1,150}", e) or e not in self.allowlist for e in live):
            raise BridgeError("Choose live cameras from your selected camera entities.")
        self.live_cameras = set(live)
        media = options.get("media_control_entity_ids", [])
        if not isinstance(media, list) or len(media) > 20 or len(set(media)) != len(media) or any(not isinstance(e, str) or not re.fullmatch(r"media_player\.[a-z0-9_]{1,150}", e) or e not in self.allowlist for e in media):
            raise BridgeError("Choose media controls from your selected media players.")
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
        self.button_sources = {}
        self.native_button_sources = set()
        self.button_status = {}
        self.stream = None
        if self.outbox_file.exists():
            if self.outbox_file.stat().st_size > 512000:
                raise BridgeError("The local pending readings file is too large. Stop and check the local installation.")
            saved = json.loads(self.outbox_file.read_text())
            self.pending = {k:v for k,v in saved.items() if isinstance(v, dict) and v.get("entity_id") in self.allowlist and k in (v["entity_id"], pending_key(v)) and safe_reading(v) and 0 <= self.clock()-utc_timestamp(v.get("observed_at")) < MAX_OUTBOX_AGE}

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
            selected = {d["entity_id"] for d in self.tracked if self.collectable(d)}
            readings = [observation(s) for s in eligible if s["entity_id"] in selected and safe_reading(s) and (not reading_class(s).startswith("health_") or any(d["entity_id"] == s["entity_id"] and d.get("collect_health") is True for d in self.tracked))][:250]
        except BridgeError:
            self.cloud("sync", {"ha_connected": False, "catalog": [], "states": [], "version": VERSION})
            raise
        result = self.cloud("sync", {"ha_connected": True, "catalog": catalog, "states": readings, "version": VERSION})
        self.tracked = result["tracked"]

    def collectable(self, device):
        # Personal values leave HA only after a person explicitly connects the source.
        state = self.current.get(device["entity_id"])
        health = device.get("kind") == "health" or state and reading_class(state).startswith("health_") or str(self.pending.get(device["entity_id"], {}).get("device_class", "")).startswith("health_")
        return not health or device.get("collect_health") is True

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
            if entity.startswith("weather."):
                self.wake.set()
                return
            if entity not in {d["entity_id"] for d in self.tracked if self.collectable(d)}:
                return
            key = pending_key(clean)
            if key not in self.pending and len(self.pending) >= MAX_PENDING:
                self.dropped += 1
                return
            # Buttons keep each action; continuous sensors keep their latest state.
            self.pending[key] = clean
            self.save_pending()
            self.wake.set()

    def accept_button_event(self, event):
        # Only an explicitly selected physical source can create a normalised press.
        if not isinstance(event, dict) or event.get("event_type") != "homekind_button":
            return
        data = event.get("data", {})
        if not isinstance(data, dict):
            return
        source = data.get("source_entity_id")
        if not isinstance(source, str):
            return
        entity = self.button_sources.get(source)
        action = data.get("action")
        at = event.get("time_fired")
        if source in self.native_button_sources or not entity or entity not in self.allowlist or not isinstance(action, str) or action not in BUTTON_ACTIONS or not -5 <= self.clock() - utc_timestamp(at) <= 10:
            return
        previous = self.current.get(entity)
        if not previous:
            return
        self.accept_state({**previous, "state": at, "last_updated": at, "last_changed": at,
                           "attributes": {**previous["attributes"], "event_type": action}})

    def shared_weather(self):
        # Only locally selected weather entities; forecasts, locations, URLs and
        # arbitrary integration attributes never leave Home Assistant.
        return [{**observation(s), "name": catalog_entry(s)["name"], "source": str(self.registry.get(s["entity_id"], {}).get("ha_platform", ""))[:80]}
                for s in self.current.values() if s["entity_id"].startswith("weather.") and s["entity_id"] in self.allowlist and safe_entity(s) and safe_reading(s)][:8]

    def stream_sync(self):
        with self.lock:
            tracked = {d["entity_id"] for d in self.tracked if self.collectable(d)} & self.allowlist
            self.pending = {k:v for k,v in self.pending.items() if v.get("entity_id") in tracked and 0 <= self.clock()-utc_timestamp(v.get("observed_at")) < MAX_OUTBOX_AGE}
            if self.stream_connected:
                # Heartbeats re-observe the current cache only while the socket is healthy.
                for state in list(self.current.values()):
                    if state["entity_id"] in tracked and not any(v["entity_id"] == state["entity_id"] for v in self.pending.values()) and len(self.pending) < MAX_PENDING:
                        clean = observation(state)
                        clean["observed_at"] = self.stamp()
                        clean["event_id"] = hashlib.sha256(json.dumps({k:v for k,v in clean.items() if k != "observed_at"}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                        self.pending[pending_key(clean)] = clean
                # Persist one heartbeat batch, rather than fsync once per cached entity.
                self.save_pending()
            connected = self.stream_connected
            catalog = [{**catalog_entry(s), **self.registry.get(s["entity_id"], {})} for s in self.current.values() if safe_entity(s) and s["entity_id"] in self.allowlist]
            batch = list(self.pending.values())[:MAX_PENDING]
            payload = {"ha_connected": connected, "catalog": catalog, "states": batch if connected else [], "version": VERSION,
                       "bridge_info": {"read_only": self.read_only, "stream_connected": connected, "observed_at": self.stamp(), "queue_size": len(self.pending), "queue_dropped": self.dropped, "weather": self.shared_weather() if connected else []}}
        result = self.cloud("sync", payload)
        with self.lock:
            self.tracked = result["tracked"]
            self.ready.set()
            if connected:
                for sent in batch:
                    key = pending_key(sent)
                    if self.pending.get(key, {}).get("event_id") == sent["event_id"]:
                        self.pending.pop(key, None)
                    # Migrate a pending 0.10.0 button entry using its original identifier.
                    elif self.pending.get(sent["entity_id"], {}).get("event_id") == sent["event_id"]:
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
        media_worker = None
        try:
            from media_control import MediaControls
            media_worker = MediaControls(self)
            media_worker.start()
        except Exception:
            logging.warning("Media controls could not start; home readings continue.")
        camera_worker = None
        if self.live_cameras:
            try:
                from camera_relay import CameraRelay
                camera_worker = CameraRelay(self)
                camera_worker.start()
            except Exception:
                camera_worker = None
                logging.warning("Live camera viewing could not start; home readings continue.")
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
            if media_worker:
                media_worker.join(timeout=2)
            if camera_worker:
                camera_worker.stop()
                camera_worker.join(timeout=2)
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

    def discover_buttons(self, websocket):
        for source, virtual in self.c.button_sources.items():
            identity = self.c.registry.get(source, {})
            device = identity.get("ha_device_id")
            if identity.get("ha_platform") != "mqtt" or not device:
                continue
            # A real selected button event entity already supplies these events.
            if any(e != virtual and s.get("attributes", {}).get("device_class") == "button"
                   and self.c.registry.get(e, {}).get("ha_device_id") == device
                   for e, s in self.c.current.items()):
                self.c.button_status[virtual] = {"name": self.c.current[virtual]["attributes"]["friendly_name"], "message": "Use the existing Home Assistant button event reading."}
                continue
            for kind, command in (("triggers", "device_automation/trigger/list"), ("mqtt", "mqtt/device/debug_info")):
                ident = self.next_id
                self.next_id += 1
                self.button_requests[ident] = (source, kind)
                websocket.send(json.dumps({"id": ident, "type": command, "device_id": device}))

    def button_result(self, websocket, message):
        ident = message.get("id")
        if ident in self.button_requests:
            source, kind = self.button_requests.pop(ident)
            self.button_discovery.setdefault(source, {})[kind] = message.get("result") if message.get("success") is True else None
            data = self.button_discovery[source]
            if "triggers" not in data or "mqtt" not in data:
                return
            virtual = self.c.button_sources[source]
            device = self.c.registry.get(source, {}).get("ha_device_id")
            rows = mqtt_button_bindings(data["mqtt"], data["triggers"], device)
            desired = {}
            for row in rows:
                key = (source, row["topic"], row["json_action"])
                desired.setdefault(key, {})[row["payload"]] = row["action"]
            for key, sub_id in list(self.button_subscriptions.items()):
                if key[0] == source:
                    self.button_routes[sub_id]["actions"] = desired.get(key, {})
            for key, actions in desired.items():
                if key not in self.button_subscriptions:
                    sub_id = self.next_id
                    self.next_id += 1
                    self.button_subscriptions[key] = sub_id
                    self.button_routes[sub_id] = {"source": source, "topic": key[1], "json_action": key[2], "actions": actions, "active": False}
                    websocket.send(json.dumps({"id": sub_id, "type": "mqtt/subscribe", "topic": key[1], "qos": 0}))
            self.update_button_status(source)
        elif ident in self.button_routes:
            route = self.button_routes[ident]
            route["active"] = message.get("success") is True
            # Failed subscriptions can be retried at the next discovery, without interrupting sensors.
            if not route["active"]:
                key = (route["source"], route["topic"], route["json_action"])
                self.button_subscriptions.pop(key, None)
                self.button_routes.pop(ident, None)
            self.update_button_status(route["source"])

    def update_button_status(self, source):
        virtual = self.c.button_sources[source]
        actions = sorted({a for r in self.button_routes.values() if r["source"] == source and r["active"] for a in r["actions"].values()})
        if actions:
            self.c.native_button_sources.add(source)
        else:
            self.c.native_button_sources.discard(source)
        self.c.button_status[virtual] = {"name": self.c.current[virtual]["attributes"]["friendly_name"], "actions": actions,
            "message": "Connected directly to Home Assistant. Ready for a physical press." if actions else "No supported press input connected yet. Try each button action in Home Assistant; discovery refreshes automatically. If Home Assistant reports a permission error, check the connector's existing local account."}

    def button_message(self, message):
        route = self.button_routes.get(message.get("id"))
        event = message.get("event")
        if not route or not route["active"] or not isinstance(event, dict) or event.get("retain") is not False or event.get("topic") != route["topic"]:
            return
        source = route["source"]
        virtual = self.c.button_sources.get(source)
        if not virtual or virtual not in self.c.allowlist:
            return
        payload = event.get("payload")
        if not isinstance(payload, str) or len(payload) > 256:
            return
        if route["json_action"]:
            try:
                body = json.loads(payload)
            except (ValueError, TypeError):
                return
            payload = body.get("action") if isinstance(body, dict) else None
        action = route["actions"].get(payload) if isinstance(payload, str) else None
        if not action:
            return
        with self.c.lock:
            previous = self.c.current.get(virtual)
            if not previous:
                return
            # HA forwards live, non-retained MQTT messages. Historical logs are never replayed.
            at = self.c.stamp()
            if utc_timestamp(at) <= utc_timestamp(previous.get("state")):
                at = dt.datetime.fromtimestamp(utc_timestamp(previous["state"]) + 0.000001, dt.timezone.utc).isoformat()
            self.c.accept_state({**previous, "state": at, "last_updated": at, "last_changed": at,
                                 "attributes": {**previous["attributes"], "event_type": action}})

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
            self.c.button_sources = {}
            self.c.native_button_sources = set()
            self.c.button_status = {}
            for button in button_candidates(states, self.c.stamp()):
                if button["entity_id"] in self.c.allowlist:
                    self.c.button_sources[button["attributes"]["source_entity_id"]] = button["entity_id"]
                    self.c.accept_state(button)
            for state in states:
                self.c.accept_state(state)
            self.c.stream_connected = True
            self.c.next_sync = 0
            self.c.wake.set()
        for ident, kind in [(2, "entity"), (3, "device"), (4, "area")]:
            websocket.send(json.dumps({"id": ident, "type": "config/" + kind + "_registry/list"}))
        if self.c.button_sources:
            websocket.send(json.dumps({"id": 5, "type": "subscribe_events", "event_type": "homekind_button"}))
        registries = {}
        self.next_id = 10
        self.button_requests, self.button_discovery = {}, {}
        self.button_routes, self.button_subscriptions = {}, {}
        refresh_buttons_at = 0
        while not self.stopped.is_set():
            if 2 in registries and self.c.clock() >= refresh_buttons_at:
                # Clear timed-out discovery requests. Existing working subscriptions stay in place.
                self.button_requests.clear()
                self.button_discovery.clear()
                self.discover_buttons(websocket)
                refresh_buttons_at = self.c.clock() + 30
            try:
                message = self.receive(websocket)
            except TimeoutError:
                # websockets ping/pong keeps idle quiet sensors current.
                continue
            if message.get("type") == "result" and message.get("id") in (2, 3, 4):
                if message.get("success") and isinstance(message.get("result"), list):
                    registries[message["id"]] = message["result"]
                    self.registry(registries)
            elif message.get("type") == "result":
                self.button_result(websocket, message)
            elif message.get("type") == "event" and message.get("id") in self.button_routes:
                self.button_message(message)
            elif message.get("type") == "event" and message.get("id") == 5:
                self.c.accept_button_event(message.get("event"))
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
                if not isinstance(entity, dict) or entity.get("entity_id") not in self.c.allowlist and entity.get("entity_id") not in self.c.button_sources:
                    continue
                device = devices.get(entity.get("device_id"), {})
                area_id = entity.get("area_id") or device.get("area_id")
                self.c.registry[entity["entity_id"]] = {k: str(v)[:200] for k,v in {
                    "ha_unique_id": entity.get("unique_id"), "ha_platform": entity.get("platform"),
                    "ha_device_id": entity.get("device_id"), "ha_area_id": area_id,
                    "ha_area_name": areas.get(area_id, {}).get("name"), "ha_entity_category": entity.get("entity_category")}.items() if v}
            for source, virtual in self.c.button_sources.items():
                if source in self.c.registry:
                    self.c.registry[virtual] = {k:v for k,v in {**self.c.registry[source], "ha_unique_id": "homekind-button-" + source}.items() if k != "ha_entity_category"}

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
