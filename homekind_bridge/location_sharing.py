"""Person-approved GPS sources; separate from household sensors and history.

Uses the existing configuration HTTP poll. No location scan while disabled;
candidate metadata only during a two-minute user search. Never writes HA state.
"""
import datetime as dt
import hashlib
import json
import math
import re
import time

from bridge import VERSION
from device_onboarding import LocalHome

ENTITY = re.compile(r"device_tracker\.[a-z0-9_]{1,150}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


def identity(entity, metadata):
    row = metadata.get(entity, {})
    if not row.get("ha_unique_id"):
        return None
    return hashlib.sha256(json.dumps({k: row.get(k, "") for k in ("ha_unique_id", "ha_device_id", "ha_platform")}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def candidates(states, metadata):
    rows = []
    for state in states[:20000] if isinstance(states, list) else []:
        if not isinstance(state, dict) or not ENTITY.fullmatch(str(state.get("entity_id", ""))):
            continue
        entity = state["entity_id"]
        attrs = state.get("attributes", {})
        fingerprint = identity(entity, metadata)
        if not fingerprint or attrs.get("source_type") != "gps":
            continue
        name = str(attrs.get("friendly_name") or entity).replace("\n", " ").replace("\r", " ")[:80]
        rows.append({"entity_id": entity, "fingerprint": fingerprint, "name": name})
    return sorted(rows, key=lambda x: (x["name"].casefold(), x["entity_id"]))[:80]


def report(state, source, metadata, now):
    if not isinstance(state, dict) or state.get("entity_id") != source["entity_id"] or identity(source["entity_id"], metadata) != source["fingerprint"]:
        return None
    attrs = state.get("attributes", {})
    if not isinstance(attrs, dict) or attrs.get("source_type") != "gps":
        return None
    try:
        if not isinstance(state.get("last_updated"), str):
            return None
        at = dt.datetime.fromisoformat(state["last_updated"].replace("Z", "+00:00"))
        if at.tzinfo is None or not now - 7*86400 <= at.timestamp() <= now + 60:
            return None
        accuracy = attrs.get("gps_accuracy")
        latitude, longitude = attrs.get("latitude"), attrs.get("longitude")
        usable = all(type(v) in (int, float) and math.isfinite(v) for v in (accuracy, latitude, longitude)) and 0 <= accuracy <= 200 and -90 <= latitude <= 90 and -180 <= longitude <= 180
    except (KeyError, ValueError, TypeError, OverflowError):
        return None
    status = state.get("state")
    status = "unknown" if not usable or status in (None, "", "unknown", "unavailable") else "home" if status == "home" else "away"
    result = {k: source[k] for k in ("subject_id", "entity_id", "fingerprint", "revision")}
    result.update(at=at.astimezone(dt.timezone.utc).isoformat(), state=status)
    # Home/away mode sends no coordinates at all. Approximate mode is rounded
    # here AND by the backend. No bearing, speed, place name or route is sent.
    if source["mode"] == "approximate" and status != "unknown":
        result.update(latitude=round(latitude, 3), longitude=round(longitude, 3), accuracy=math.ceil(accuracy))
    return result


class LocationSharing:
    def __init__(self, manager, clock=time.time, home_factory=LocalHome):
        self.manager, self.clock, self.home_factory = manager, clock, home_factory
        self.home = None
        self.inspect_at = 0
        self.clear()

    def clear(self):
        self.sources = []
        self.discover = False
        self.until = 0
        self.management_id = ""

    def accept(self, response, options):
        try:
            if not options.get("managed_settings") or not isinstance(response, dict) or set(response) != {"protocol", "management_id", "discover", "sources"} or response["protocol"] != 1 or response["management_id"] != options.get("management_id") or type(response["discover"]) is not bool or not isinstance(response["sources"], list) or len(response["sources"]) > 30:
                self.clear()
                return
            selected = []
            for row in response["sources"]:
                if not isinstance(row, dict) or set(row) != {"subject_id", "entity_id", "fingerprint", "revision", "mode"} or not UUID.fullmatch(str(row["subject_id"])) or not ENTITY.fullmatch(str(row["entity_id"])) or not HASH.fullmatch(str(row["fingerprint"])) or type(row["revision"]) is not int or row["revision"] < 1 or row["mode"] not in ("home_away", "approximate"):
                    self.clear()
                    return
                selected.append(dict(row))
            self.sources, self.discover = selected, response["discover"]
            self.management_id = response["management_id"]
            self.until = self.clock() + 45  # A server outage cannot extend a cached request.
        except (KeyError, TypeError, ValueError):
            self.clear()

    def prepare(self, options):
        current = self.manager.connector
        available = bool(current and current.stream_connected and options.get("managed_settings"))
        payload = {"protocol": 1, "version": VERSION, "management_id": options.get("management_id", ""), "available": available, "candidates": [], "reports": []}
        if not available or self.management_id != options.get("management_id") or self.clock() > self.until:
            self.clear()
            return payload
        if not self.sources and not self.discover:
            return payload
        try:
            if self.home is None or self.home.c is not current:
                self.home = self.home_factory(current)
                self.inspect_at = 0
            if self.clock() >= self.inspect_at:
                self.home.inspect()
                self.inspect_at = self.clock() + 60
            if self.discover:
                payload["candidates"] = candidates(current.ha("states"), self.home.metadata)
            for source in self.sources:
                row = report(current.ha("states/" + source["entity_id"]), source, self.home.metadata, self.clock())
                if row:
                    payload["reports"].append(row)
            # A concurrent local opt-out/replacement wins over an in-flight read.
            if options != self.manager.options() or current is not self.manager.connector:
                self.clear()
                payload["reports"], payload["candidates"] = [], []
        except Exception:
            payload["available"] = False
            payload["reports"], payload["candidates"] = [], []
        return payload
