"""Expiring, confirmed device enrolment. No arbitrary HA action or MQTT topic input."""
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import time
import uuid

from bridge import BridgeError, CLOUD_URL, VERSION, Connector, atomic_json, button_candidates, catalog_entry, safe_entity, utc_timestamp

PROTOCOL = 1
MAX_CANDIDATES = 500
META = ("ha_unique_id", "ha_platform", "ha_device_id", "ha_area_id", "ha_area_name", "ha_entity_category")


def uuid_ok(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value))


def fingerprint(row):
    identity = {k: row.get(k, "") for k in ("entity_id", "device_class", "ha_unique_id", "ha_device_id")}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def candidates(states, metadata=None, baseline=None):
    if not isinstance(states, list) or len(states) > 20000:
        raise BridgeError("Home Assistant device discovery is unavailable.")
    metadata = metadata or {}
    rows = []
    for state in states + button_candidates(states, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())):
        if not isinstance(state, dict) or not safe_entity(state) or state["entity_id"].startswith("weather."):
            continue
        # Only a small metadata catalogue leaves the home during an explicit session.
        row = catalog_entry(state)
        source = state.get("attributes", {}).get("source_entity_id", state["entity_id"])
        row.update({k: str(v)[:200] for k, v in metadata.get(source, {}).items() if k in META and v})
        if source != state["entity_id"]:
            row["ha_unique_id"] = "homekind-button-" + source
        row["fingerprint"] = fingerprint(row)
        row["new"] = baseline is not None and state["entity_id"] not in baseline
        rows.append(row)
    rows.sort(key=lambda r: (not r["new"], r["name"].casefold(), r["entity_id"]))
    rows = rows[:MAX_CANDIDATES]
    while rows and len(json.dumps(rows, ensure_ascii=False).encode()) > 250000:
        rows.pop()
    return rows


def z2m_route(info):
    """Use HA's actual MQTT discovery payload, never a guessed or cloud-provided topic."""
    entries = info.get("entities", []) if isinstance(info, dict) else []
    if isinstance(entries, dict):
        entries = list(entries.values())
    if not isinstance(entries, list):
        return None
    for row in entries[:100]:
        cfg = row.get("discovery_data", {}).get("payload", {}) if isinstance(row, dict) else {}
        if not isinstance(cfg, dict):
            continue
        device = cfg.get("device", {})
        topic = cfg.get("command_topic")
        if not isinstance(device, dict) or str(device.get("manufacturer", "")).lower() != "zigbee2mqtt" or str(device.get("model", "")).lower() != "bridge":
            continue
        if not isinstance(topic, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,199}/bridge/request/permit_join", topic) or ".." in topic or "//" in topic:
            continue
        base = topic.removesuffix("/bridge/request/permit_join")
        if cfg.get("state_topic") != base + "/bridge/info":
            continue
        try:
            if json.loads(cfg.get("payload_off", "")) != {"time": 0}:
                continue
        except (ValueError, TypeError):
            continue
        return {"kind": "zigbee2mqtt", "command_topic": topic, "response_topic": base + "/bridge/response/permit_join",
                "key": "z2m_" + hashlib.sha256(topic.encode()).hexdigest()[:24], "name": str(device.get("name") or "Zigbee2MQTT network")[:80]}
    return None


class LocalHome:
    def __init__(self, connector, connect=None):
        self.c = connector
        if connect is None:
            from websockets.sync.client import connect
        self.connect = connect
        self.metadata = {}
        self.routes = {}
        self.excluded_devices = set()

    def socket(self):
        url = "ws://supervisor/core/websocket" if self.c.supervisor else self.c.ha_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1) + "/websocket"
        return self.connect(url, proxy=None, compression=None, open_timeout=5, close_timeout=2, ping_interval=10, ping_timeout=10, max_size=4_000_000)

    @staticmethod
    def receive(ws):
        value = json.loads(ws.recv(timeout=5))
        if not isinstance(value, dict):
            raise BridgeError("Home Assistant discovery is unavailable.")
        return value

    def auth(self, ws):
        if self.receive(ws).get("type") != "auth_required":
            raise BridgeError("Home Assistant discovery is unavailable.")
        ws.send(json.dumps({"type": "auth", "access_token": self.c.ha_token}))
        if self.receive(ws).get("type") != "auth_ok":
            raise BridgeError("Home Assistant discovery is unavailable.")

    def query(self, ws, ident, kind, **args):
        ws.send(json.dumps({"id": ident, "type": kind, **args}))
        for _ in range(20):
            message = self.receive(ws)
            if message.get("id") == ident and message.get("type") == "result":
                if message.get("success") is not True:
                    raise BridgeError("Home Assistant discovery is unavailable.")
                return message.get("result")
        raise BridgeError("Home Assistant discovery is unavailable.")

    def inspect(self):
        services = self.c.ha("services")
        self.routes = {}
        if isinstance(services, list) and any(s.get("domain") == "zha" and "permit" in s.get("services", {}) for s in services if isinstance(s, dict)):
            self.routes["zha"] = {"key": "zha", "name": "Zigbee network", "kind": "zha"}
        self.metadata = {}
        try:
            with self.socket() as ws:
                self.auth(ws)
                entities = self.query(ws, 1, "config/entity_registry/list")
                devices = self.query(ws, 2, "config/device_registry/list")
                areas = self.query(ws, 3, "config/area_registry/list")
                if not all(isinstance(rows, list) and len(rows) <= 20000 for rows in (entities, devices, areas)):
                    raise BridgeError("Home Assistant discovery is unavailable.")
                device_map = {d["id"]: d for d in devices if isinstance(d, dict) and "id" in d}
                area_map = {a["area_id"]: a for a in areas if isinstance(a, dict) and "area_id" in a}
                for e in entities:
                    if not isinstance(e, dict) or not isinstance(e.get("entity_id"), str) or e.get("disabled_by"):
                        continue
                    device = device_map.get(e.get("device_id"), {})
                    area = e.get("area_id") or device.get("area_id")
                    self.metadata[e["entity_id"]] = {k: str(v)[:200] for k, v in {"ha_unique_id": e.get("unique_id"), "ha_platform": e.get("platform"), "ha_device_id": e.get("device_id"), "ha_area_id": area, "ha_area_name": area_map.get(area, {}).get("name"), "ha_entity_category": e.get("entity_category")}.items() if v}
                bridges = [d for d in devices if isinstance(d, dict) and str(d.get("manufacturer", "")).lower() == "zigbee2mqtt" and str(d.get("model", "")).lower() == "bridge"]
                self.excluded_devices = {d["id"] for d in bridges if "id" in d}
                for i, device in enumerate(bridges[:4]):
                    try:
                        route = z2m_route(self.query(ws, i + 10, "mqtt/device/debug_info", device_id=device["id"]))
                        if route:
                            self.routes[route["key"]] = route
                    except BridgeError:
                        continue
        except Exception:
            # Plain device discovery and a known ZHA route can still work.
            pass
        return [{k: route[k] for k in ("key", "name", "kind")} for route in self.routes.values()]

    def pair(self, route, duration):
        if type(duration) is not int or not 0 <= duration <= 90:
            raise BridgeError("Choose a short pairing window.")
        if route.get("kind") == "zha" and route.get("key") == "zha":
            self.c.ha("services/zha/permit", {"duration": duration})
            return
        expected = z2m_route({"entities": [{"discovery_data": {"payload": {"device": {"manufacturer": "Zigbee2MQTT", "model": "Bridge"}, "command_topic": route.get("command_topic"), "state_topic": str(route.get("command_topic", "")).removesuffix("/request/permit_join") + "/info", "payload_off": '{"time":0}'}}}]})
        if route.get("kind") != "zigbee2mqtt" or expected is None or any(route.get(k) != expected[k] for k in ("key", "command_topic", "response_topic")):
            raise BridgeError("Choose a locally discovered Zigbee network.")
        transaction = secrets.token_hex(12)
        with self.socket() as ws:
            self.auth(ws)
            self.query(ws, 1, "mqtt/subscribe", topic=route["response_topic"], qos=0)
            self.c.ha("services/mqtt/publish", {"topic": route["command_topic"], "payload": json.dumps({"time": duration, "transaction": transaction}), "qos": 0, "retain": False})
            for _ in range(20):
                message = self.receive(ws)
                event = message.get("event", {})
                if message.get("id") != 1 or event.get("topic") != route["response_topic"] or event.get("retain") is not False:
                    continue
                payload = event.get("payload")
                if not isinstance(payload, str) or len(payload) > 2048:
                    continue
                answer = json.loads(payload)
                if isinstance(answer, dict) and answer.get("transaction") == transaction:
                    if answer.get("status") == "ok" and answer.get("data", {}).get("time") == duration:
                        return
                    raise BridgeError("Pairing could not be confirmed.")
        raise BridgeError("Pairing could not be confirmed.")


class DeviceOnboarding:
    def __init__(self, manager, home_factory=LocalHome, clock=time.time):
        self.manager, self.home_factory, self.clock = manager, home_factory, clock
        self.path = manager.directory / "onboarding.json"
        if self.path.exists() and self.path.stat().st_size > 2_000_000:
            raise BridgeError("Check local onboarding settings.")
        self.saved = json.loads(self.path.read_text()) if self.path.exists() else {}
        if not isinstance(self.saved, dict):
            raise BridgeError("Check local onboarding settings.")
        self.home = None
        self.client = None
        self.inspect_at = 0
        self.available = False
        self.rows = []
        self.error = ""

    def save(self):
        atomic_json(self.path, self.saved)

    def close_pairing(self):
        if self.saved.get("pair_dispatched") and not self.saved.get("pair_closed"):
            try:
                if self.home and self.saved.get("route"):
                    self.home.pair(self.saved["route"], 0)
                    self.saved["pair_closed"] = True
                    self.save()
            except Exception:
                # The radio's own bounded timer also closes the window.
                pass

    def authorised(self, directive, operation):
        result = self.manager.request(CLOUD_URL + "/onboarding-authorize", self.client.credentials["token"], {"bridge_id": self.client.credentials["bridge_id"], "session_id": directive["session_id"], "management_id": directive["management_id"], "operation": operation, "revision": directive.get("revision", 0)}, timeout=5)
        return isinstance(result, dict) and result.get("authorised") is True

    def act(self, directive, options):
        if not isinstance(directive, dict) or not uuid_ok(directive.get("session_id")) or directive.get("management_id") != options.get("management_id") or not options.get("managed_settings"):
            self.close_pairing()
            return
        deadline = utc_timestamp(directive.get("expires_at"))
        if not self.clock() < deadline <= self.clock() + 310:
            self.close_pairing()
            return
        if directive["session_id"] != self.saved.get("session_id"):
            self.close_pairing()
            self.saved = {"session_id": directive["session_id"], "management_id": options["management_id"], "expires_at": deadline, "baseline": [r["entity_id"] for r in self.rows], "pair_dispatched": False, "pair_closed": True}
            self.save()
            self.inspect_at = 0
            self.error = ""
        # A retried directive never extends the original session or radio timer.
        if self.clock() >= self.saved["expires_at"]:
            self.close_pairing()
            return
        if directive.get("mode") == "zigbee" and not self.saved.get("pair_dispatched") and directive.get("phase") != "approved":
            route = self.home.routes.get(directive.get("adapter"))
            end = utc_timestamp(directive.get("pair_until"))
            duration = min(90, math.floor(end - self.clock()))
            if route and duration > 0 and self.authorised(directive, "pair"):
                with self.manager.lock:
                    if self.manager.options().get("management_id") != options["management_id"]:
                        return
                    self.saved.update(route=route, pair_dispatched=True, pair_closed=False, pair_until=end)
                    self.save()  # Persist before the physical action; a timeout is never retried.
                try:
                    self.home.pair(route, duration)
                    self.saved["pair_confirmed"] = True
                    self.error = ""
                except Exception:
                    self.error = "pairing_not_confirmed"
                self.save()
            elif not route:
                self.error = "pairing_unavailable"
        if self.saved.get("pair_until", float("inf")) <= self.clock():
            self.close_pairing()
        if directive.get("phase") != "approved":
            return
        approved = directive.get("approved")
        if not isinstance(approved, list) or not 1 <= len(approved) <= 40 or type(directive.get("revision")) is not int or directive["revision"] != 1:
            raise BridgeError("Check confirmed device choices.")
        available = {r["entity_id"]: r for r in self.rows}
        ids = []
        for choice in approved:
            entity = choice.get("entity_id") if isinstance(choice, dict) else None
            if entity not in available or choice.get("fingerprint") != available[entity]["fingerprint"] or not uuid_ok(choice.get("device_id")):
                self.error = "device_changed"
                return
            ids.append(entity)
        if len(set(ids)) != len(ids):
            raise BridgeError("Choose each device reading once.")
        if not self.authorised(directive, "enrol"):
            self.close_pairing()
            return
        self.close_pairing()
        with self.manager.lock:
            current = self.manager.options()
            if current != options:
                self.error = "settings_changed"
                return
            selected = list(dict.fromkeys(current["entity_ids"] + ids))
            if len(selected) > 250:
                self.error = "device_limit"
                return
            new_id = secrets.token_hex(16)
            receipt = {"session_id": directive["session_id"], "management_id": options["management_id"], "new_management_id": new_id, "revision": 1, "entity_ids": ids}
            self.saved["appended_entity_ids"] = [entity for entity in ids if entity not in current["entity_ids"]]
            self.saved["enrol_pending"] = receipt
            self.save()
            atomic_json(self.manager.settings, {**current, "entity_ids": selected, "management_id": new_id, "applied_revision": 0})
            self.saved["receipt"] = receipt
            self.saved.pop("enrol_pending", None)
            self.save()
            self.manager.changed.set()

    def step(self):
        with self.manager.lock:
            if not self.manager.paired():
                return
            options = self.manager.options()
            self.client = Connector(options, self.manager.directory, request=self.manager.request)
            pending = self.saved.get("enrol_pending")
            if isinstance(pending, dict) and pending.get("new_management_id") == options.get("management_id") and set(pending.get("entity_ids", [])) <= set(options["entity_ids"]):
                self.saved["receipt"] = pending
                self.saved.pop("enrol_pending", None)
                self.save()
        if self.home is None:
            self.home = self.home_factory(self.client)
        else:
            self.home.c = self.client
        active = bool(self.saved.get("session_id") and self.saved.get("expires_at", 0) > self.clock() and not self.saved.get("receipt"))
        if not options.get("managed_settings") or self.saved.get("management_id") and self.saved["management_id"] != options.get("management_id") and not self.saved.get("receipt"):
            self.close_pairing()
            active = False
        if self.saved.get("pair_until", float("inf")) <= self.clock() or self.saved.get("expires_at", float("inf")) <= self.clock():
            self.close_pairing()
        adapters = []
        if options.get("managed_settings"):
            try:
                if self.clock() >= self.inspect_at:
                    self.home.inspect()
                    self.inspect_at = self.clock() + (12 if active else 60)
                adapters = [{k: r[k] for k in ("key", "name", "kind")} for r in self.home.routes.values()]
                states = self.client.ha("states")
                self.rows = candidates(states, self.home.metadata, set(self.saved.get("baseline", [])) if active else None)
                self.rows = [r for r in self.rows if r.get("ha_device_id") not in self.home.excluded_devices]
                self.available = True
                if self.error == "home_unavailable":
                    self.error = ""
            except Exception:
                self.available = False
                self.error = "home_unavailable"
        else:
            self.available = False
            self.rows = []
        receipt = self.saved.get("receipt")
        if receipt and receipt.get("new_management_id") != options.get("management_id"):
            receipt = None
        report = {"bridge_id": self.client.credentials["bridge_id"], "protocol": PROTOCOL, "version": VERSION, "management_id": options.get("management_id", ""), "available": self.available,
                  "adapters": adapters, "session_id": self.saved.get("session_id"), "candidates": self.rows if active else [], "pair_confirmed": bool(self.saved.get("pair_confirmed") and not self.saved.get("pair_closed")), "error": self.error, "receipt": receipt}
        response = self.manager.request(CLOUD_URL + "/onboarding", self.client.credentials["token"], report, timeout=5)
        if not isinstance(response, dict):
            raise BridgeError("Device setup is reconnecting.")
        if response.get("receipt_accepted") and receipt:
            if response.get("receipt_status") in ("cancelled", "expired"):
                # A revoked/expired setup cannot leave its new local selection behind.
                with self.manager.lock:
                    current = self.manager.options()
                    if current.get("management_id") == receipt["new_management_id"]:
                        removed = set(self.saved.get("appended_entity_ids", []))
                        atomic_json(self.manager.settings, {**current, "entity_ids": [entity for entity in current["entity_ids"] if entity not in removed], "management_id": secrets.token_hex(16), "applied_revision": 0})
                        self.manager.changed.set()
            self.saved["receipt_accepted"] = True
            self.save()
        directive = response.get("directive")
        if directive is None:
            self.close_pairing()
            if not self.saved.get("receipt"):
                self.saved.pop("session_id", None)
                self.save()
            return
        self.act(directive, options)

    def run(self):
        while not self.manager.stopped.is_set():
            try:
                self.step()
            except Exception:
                # A cloud outage never extends a network's bounded pairing timer.
                self.close_pairing()
            self.manager.stopped.wait(3 if self.saved.get("session_id") and not self.saved.get("receipt_accepted") else 8)
        self.close_pairing()
