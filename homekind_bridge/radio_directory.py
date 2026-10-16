"""Free radio via Home Assistant. Stream addresses never leave Home Assistant."""
import hashlib
import json
import logging
import re
import threading
import time

MAX_STATIONS = 60
RADIO_ID = re.compile(r"^media-source://radio_browser/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})$", re.I)


def station_choice(node):
    if not isinstance(node, dict) or node.get("can_play") is not True:
        return None
    content_id = node.get("media_content_id")
    match = RADIO_ID.fullmatch(content_id) if isinstance(content_id, str) else None
    content_type = node.get("media_content_type")
    label = node.get("title")
    if not match or not isinstance(content_type, str) or not (re.fullmatch(r"audio/[a-z0-9.+-]{1,40}", content_type) or content_type == "application/ogg"):
        return None
    if not isinstance(label, str) or re.search(r"[a-z][a-z0-9+.-]*://|[\x00-\x08\x0b-\x1f]", label, re.I):
        return None
    label = " ".join(label.split())[:80]
    if not label:
        return None
    canonical_id = "media-source://radio_browser/" + match[1].lower()
    key = hashlib.sha256(("radio:" + canonical_id).encode()).hexdigest()
    return {"key": key, "label": label, "value": {"media_content_id": canonical_id, "media_content_type": content_type}}


def station_priority(choice):
    name = choice["label"].casefold()
    preferred = [r"\babc\b.*\bmelbourne\b", r"\babc\b.*\bclassic\b", r"\babc\b.*\bjazz\b", r"\babc\b.*\bnews\b", r"\bdouble\s*j\b", r"\btriple\s*j\b", r"\bsbs\b", r"\b3aw\b", r"\bgold\s*104"]
    rank = next((i for i, pattern in enumerate(preferred) if re.search(pattern, name)), 20)
    return rank, name, choice["value"]["media_content_type"] != "audio/mpeg", choice["key"]


class RadioDirectory(threading.Thread):
    """Slow directory discovery runs independently of controls and sensor updates."""

    def __init__(self, connector, selected):
        super().__init__(name="homekind-radio", daemon=True)
        self.connector = connector
        self.selected = set(selected)
        self.lock = threading.Lock()
        self.status = "loading"
        self.country = "AU"
        self.stations = {}

    def catalogue(self):
        with self.lock:
            return {"status": self.status, "country": self.country,
                    "stations": [{"key": key, "label": item["label"]} for key, item in self.stations.items()] if self.status == "ready" else []}

    def resolve(self, key):
        with self.lock:
            item = self.stations.get(key) if self.status == "ready" and isinstance(key, str) else None
            return dict(item["value"]) if item else None

    def set_status(self, status):
        with self.lock:
            self.status = status
            if status != "ready":
                self.stations = {}

    def candidates(self):
        c = self.connector
        with c.lock:
            if not c.stream_connected:
                return []
            rows = [(entity, dict(state)) for entity, state in c.current.items() if entity in self.selected and entity in c.allowlist]
        return [entity for entity, state in sorted(rows) if isinstance(state.get("attributes", {}).get("supported_features"), int) and not isinstance(state["attributes"]["supported_features"], bool) and state["attributes"]["supported_features"] & 512]

    def browse_media_source(self, country, connect=None):
        if not isinstance(country, str) or not re.fullmatch(r"[A-Z]{2}", country):
            raise ValueError("Invalid radio country")
        if connect is None:
            from websockets.sync.client import connect
        c = self.connector
        url = "ws://supervisor/core/websocket" if c.supervisor else c.ha_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1) + "/websocket"
        with connect(url, proxy=None, compression=None, open_timeout=10, close_timeout=3,
                     ping_interval=10, ping_timeout=10, max_size=4_000_000, max_queue=4) as socket:
            if json.loads(socket.recv(timeout=10)).get("type") != "auth_required":
                raise ValueError("Home Assistant radio authentication unavailable")
            socket.send(json.dumps({"type": "auth", "access_token": c.ha_token}))
            if json.loads(socket.recv(timeout=10)).get("type") != "auth_ok":
                raise ValueError("Home Assistant radio authentication unavailable")
            socket.send(json.dumps({"id": 1, "type": "media_source/browse_media", "media_content_id": "media-source://radio_browser/country/" + country}))
            answer = json.loads(socket.recv(timeout=20))
            if answer.get("id") != 1 or answer.get("type") != "result" or answer.get("success") is not True or not isinstance(answer.get("result"), dict):
                raise ValueError("Home Assistant radio directory unavailable")
            return answer["result"].get("children", [])

    def refresh(self):
        c = self.connector
        candidates = self.candidates()
        if not candidates:
            self.set_status("no-player")
            return
        config = c.ha("config")
        country = config.get("country")
        self.country = country if isinstance(country, str) and re.fullmatch(r"[A-Z]{2}", country) else "AU"
        if "radio_browser" not in config.get("components", []):
            self.set_status("setup")
            return
        if c.stopping():
            return
        nodes = self.browse_media_source(self.country)
        if not isinstance(nodes, list):
            self.set_status("unavailable")
            return
        choices = [choice for node in nodes[:5000] if (choice := station_choice(node))]
        ordered = sorted(choices, key=station_priority)
        unique = {}
        names = set()
        for choice in ordered:
            rank = station_priority(choice)[0]
            name = "preset:" + str(rank) if rank <= 5 else choice["label"].casefold()
            if name in names or choice["key"] in unique:
                continue
            unique[choice["key"]] = choice
            names.add(name)
            if len(unique) == MAX_STATIONS:
                break
        if unique:
            with self.lock:
                self.stations, self.status = unique, "ready"
            return
        self.set_status("unavailable")

    def run(self):
        c = self.connector
        next_refresh = 0
        while not c.stopping():
            if c.ready.wait(.2) and time.monotonic() >= next_refresh:
                try:
                    self.refresh()
                except Exception:
                    self.set_status("unavailable")
                    logging.info("Radio stations are temporarily unavailable; media controls continue.")
                next_refresh = time.monotonic() + (1800 if self.catalogue()["status"] == "ready" else 15)
            c.halt.wait(1)
