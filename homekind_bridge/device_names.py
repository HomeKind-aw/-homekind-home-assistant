"""Match Belmelo labels to locally verified Zigbee2MQTT devices; never rename HA IDs."""
import datetime as dt
import json
import re
import secrets
import time
from bridge import BridgeError, atomic_json
from device_onboarding import LocalHome


def label_ok(value):
    return (isinstance(value, str) and 1 <= len(value) <= 80 and value == value.strip()
            and value.casefold() != "bridge" and not re.search(r"[\x00-\x1f\x7f/\\#+]", value))


def expiry(payload):
    stamp = dt.datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("A timezone is required.")
    return stamp.timestamp()


def checked(payload, options, now):
    if not options.get("managed_settings") or not options.get("management_id"):
        return []
    if not isinstance(payload, dict) or set(payload) != {"protocol", "management_id", "expires_at", "devices"}:
        return []
    if type(payload["protocol"]) is not int or payload["protocol"] != 1 or payload["management_id"] != options["management_id"]:
        return []
    try:
        expires = expiry(payload)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return []
    if not now < expires <= now + 65 or not isinstance(payload["devices"], list) or len(payload["devices"]) > 250:
        return []
    result, seen = [], set()
    for row in payload["devices"]:
        if (not isinstance(row, dict) or set(row) != {"key", "entity_id", "ha_unique_id", "ha_device_id", "name"}
                or not isinstance(row["key"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["key"])
                or row["entity_id"] not in options.get("entity_ids", [])
                or not isinstance(row["ha_device_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", row["ha_device_id"])
                or not isinstance(row["ha_unique_id"], str) or not re.fullmatch(r"0x[0-9a-f]{16}_.+_zigbee2mqtt", row["ha_unique_id"])
                or not label_ok(row["name"]) or row["ha_device_id"] in seen):
            return []
        seen.add(row["ha_device_id"])
        result.append(row)
    return result


class DeviceNames:
    def __init__(self, manager, home_factory=LocalHome, clock=time.time):
        self.manager, self.home_factory, self.clock = manager, home_factory, clock
        self.path = manager.directory / "device_names.json"
        if self.path.exists() and self.path.stat().st_size > 150000:
            raise BridgeError("Check the local device-name receipts.")
        self.saved = json.loads(self.path.read_text()) if self.path.exists() else {}
        if not isinstance(self.saved, dict) or len(self.saved) > 250:
            raise BridgeError("Check the local device-name receipts.")

    def report(self):
        statuses = {"synced", "conflict", "unavailable", "unsupported", "pending"}
        return [{"key": r["key"], "status": r["status"]} for r in self.saved.values()
                if isinstance(r, dict) and isinstance(r.get("key"), str)
                and re.fullmatch(r"[0-9a-f]{64}", r["key"]) and r.get("status") in statuses][:250]

    def current(self, options, credentials, expires):
        with self.manager.lock:
            return (self.clock() < expires and options == self.manager.options()
                    and credentials == json.loads(self.manager.credentials.read_text()))

    def receipt(self, row, status):
        self.saved[row["ha_device_id"]] = {"key": row["key"], "status": status, "attempted_at": self.clock()}
        atomic_json(self.path, self.saved)

    @staticmethod
    def subscribe(home, ws, ident, topic):
        ws.send(json.dumps({"id": ident, "type": "mqtt/subscribe", "topic": topic, "qos": 0}))
        buffered = []
        for _ in range(30):
            message = home.receive(ws)
            if message.get("id") == ident and message.get("type") == "result":
                if message.get("success") is not True:
                    raise BridgeError("The Zigbee device name could not be confirmed.")
                return buffered
            if message.get("id") == ident and message.get("type") == "event":
                buffered.append(message)
        raise BridgeError("The Zigbee device name could not be confirmed.")

    @staticmethod
    def mqtt_event(home, ws, ident, topic, retained=None, size=3000000, buffered=None):
        for _ in range(30):
            message = buffered.pop(0) if buffered else home.receive(ws)
            event = message.get("event", {})
            if message.get("id") != ident or event.get("topic") != topic:
                continue
            if retained is not None and event.get("retain") is not retained:
                continue
            payload = event.get("payload")
            if isinstance(payload, str) and len(payload.encode()) <= size:
                return json.loads(payload)
        raise BridgeError("The Zigbee device name could not be confirmed.")

    def rename(self, row, options, credentials, expires):
        connector = self.manager.connector
        if connector is None:
            return "unavailable"
        home = self.home_factory(connector)
        home.inspect()
        meta = home.metadata.get(row["entity_id"], {})
        if (meta.get("ha_platform") != "mqtt" or any(meta.get(k) != row[k] for k in ("ha_unique_id", "ha_device_id"))):
            return "unsupported"
        ieee = row["ha_unique_id"].split("_", 1)[0]
        with home.socket() as ws:
            home.auth(ws)
            info = home.query(ws, 1, "mqtt/device/debug_info", device_id=row["ha_device_id"])
            entries = info.get("entities", []) if isinstance(info, dict) else []
            if isinstance(entries, dict):
                entries = list(entries.values())
            if not isinstance(entries, list) or len(entries) > 500:
                return "unsupported"
            topics = {e.get("discovery_data", {}).get("payload", {}).get("state_topic") for e in entries
                      if isinstance(e, dict) and isinstance(e.get("discovery_data"), dict)
                      and isinstance(e["discovery_data"].get("payload"), dict)
                      and e["discovery_data"]["payload"].get("unique_id") == row["ha_unique_id"]}
            routes = [r for r in home.routes.values() if r.get("kind") == "zigbee2mqtt"
                      and any(isinstance(topic, str) and topic.startswith(r["command_topic"].removesuffix("/bridge/request/permit_join") + "/") for topic in topics)]
            if len(routes) != 1:
                return "unsupported"
            base = routes[0]["command_topic"].removesuffix("/bridge/request/permit_join")
            buffered = self.subscribe(home, ws, 2, base + "/bridge/devices")
            devices = self.mqtt_event(home, ws, 2, base + "/bridge/devices", buffered=buffered)
            if not isinstance(devices, list) or len(devices) > 20000:
                return "unsupported"
            matches = [d for d in devices if isinstance(d, dict) and d.get("ieee_address") == ieee and d.get("type") != "Coordinator"]
            if len(matches) != 1:
                return "unsupported"
            old_name = matches[0].get("friendly_name")
            if old_name == row["name"]:
                return "synced"
            if any(isinstance(d, dict) and d.get("friendly_name") == row["name"] and d.get("ieee_address") != ieee for d in devices):
                return "conflict"
            if not self.current(options, credentials, expires):
                return "unavailable"
            transaction = secrets.token_hex(12)
            response_topic = base + "/bridge/response/device/rename"
            buffered = self.subscribe(home, ws, 3, response_topic)
            self.receipt(row, "pending")
            if not self.current(options, credentials, expires):
                return "unavailable"
            connector.ha("services/mqtt/publish", {"topic": base + "/bridge/request/device/rename",
                         "payload": json.dumps({"from": ieee, "to": row["name"], "homeassistant_rename": False, "transaction": transaction}),
                         "qos": 0, "retain": False})
            for _ in range(10):
                answer = self.mqtt_event(home, ws, 3, response_topic, retained=False, size=4096, buffered=buffered)
                if not isinstance(answer, dict) or answer.get("transaction") != transaction:
                    continue
                data = answer.get("data", {})
                if (answer.get("status") == "ok" and isinstance(data, dict) and data.get("to") == row["name"]
                        and data.get("from") in (ieee, old_name) and data.get("homeassistant_rename") is False):
                    return "synced"
                return "unavailable"
        return "unavailable"

    def accept(self, payload, options, credentials):
        rows = checked(payload, options, self.clock())
        if not rows:
            return
        expires = expiry(payload)
        ids = {r["ha_device_id"] for r in rows}
        self.saved = {k: v for k, v in self.saved.items() if k in ids}
        for row in rows:
            old = self.saved.get(row["ha_device_id"], {})
            if old.get("key") == row["key"] and (old.get("status") == "synced" or self.clock() - old.get("attempted_at", 0) < 300):
                continue
            if not self.current(options, credentials, expires):
                return
            try:
                status = self.rename(row, options, credentials, expires)
            except Exception:
                status = "unavailable"
            self.receipt(row, status)
            break
