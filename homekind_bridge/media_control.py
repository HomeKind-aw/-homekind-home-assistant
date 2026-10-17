"""Optional media controls. Content addresses and artwork always stay in HA."""
import hashlib
import logging
import math
import re
import threading
import time
from radio_directory import RadioDirectory

FEATURES = {"pause": 1, "volume": 4, "play": 16384}


def short_text(value, limit):
    if not isinstance(value, str) or re.search(r"[a-z][a-z0-9+.-]*://|[\x00-\x08\x0b-\x1f]", value, re.I):
        return ""
    return value.strip()[:limit]


def playback(state, choices):
    attrs = state.get("attributes", {})
    features = attrs.get("supported_features", 0)
    features = features if isinstance(features, int) and not isinstance(features, bool) else 0
    result = {"entity_id": state["entity_id"], "state": state.get("state", "unknown"),
              "features": [name for name, flag in FEATURES.items() if features & flag],
              "choices": [{"key": key, "label": item["label"]} for key, item in choices.items()]}
    if choices and features & (512 | 2048):
        result["features"].append("favourite")
    if features & 512:
        result["features"].append("radio")
    if result["state"] in ("playing", "paused", "buffering"):
        result.update(title=short_text(attrs.get("media_title"), 160), artist=short_text(attrs.get("media_artist"), 120))
    volume = attrs.get("volume_level")
    if isinstance(volume, (int, float)) and not isinstance(volume, bool) and math.isfinite(volume) and 0 <= volume <= 1:
        result["volume"] = round(volume, 3)
    return result


class MediaControls(threading.Thread):
    def __init__(self, connector):
        super().__init__(name="homekind-media", daemon=True)
        self.connector = connector
        self.selected = set(connector.options.get("media_control_entity_ids", []))
        self.choices = {}
        self.browsed_at = {}
        self.next_sync = 0
        self.last_payload = None
        self.wake = threading.Event()
        self.sync_lock = threading.Lock()
        self.radio = RadioDirectory(connector, self.selected)

    def browse(self, entity, state):
        """Keep playable IDs locally; share only opaque references and short names."""
        attrs = state.get("attributes", {})
        found = {}

        def add(label, action, value):
            label = short_text(label, 80)
            if not label or len(found) >= 24 or not isinstance(value, dict):
                return
            import json
            key = hashlib.sha256((entity + ":" + action + ":" + json.dumps(value, sort_keys=True)).encode()).hexdigest()
            found[key] = {"label": label, "action": action, "value": value}

        if isinstance(attrs.get("source_list"), list) and attrs.get("supported_features", 0) & 2048:
            for source in attrs["source_list"][:24]:
                if isinstance(source, str):
                    add(source, "select_source", {"source": source})
        if attrs.get("supported_features", 0) & 131072 and attrs.get("supported_features", 0) & 512:
            try:
                root = self.connector.ha("services/media_player/browse_media?return_response", {"entity_id": entity})
                root = root.get("service_response", {}).get(entity, {})
                nodes = root.get("children", []) if isinstance(root, dict) else []
                # Browse only an explicitly named favourites branch; never crawl a library.
                branches = [n for n in nodes if isinstance(n, dict) and re.search(r"favou?rites|my sonos", str(n.get("title", "")), re.I)]
                if branches:
                    n = branches[0]
                    response = self.connector.ha("services/media_player/browse_media?return_response", {"entity_id": entity, "media_content_id": n.get("media_content_id"), "media_content_type": n.get("media_content_type")})
                    nodes = response.get("service_response", {}).get(entity, {}).get("children", [])
                for n in nodes[:100]:
                    if isinstance(n, dict) and n.get("can_play") is True and isinstance(n.get("media_content_id"), str) and len(n["media_content_id"]) <= 2000 and isinstance(n.get("media_content_type"), str):
                        add(n.get("title"), "play_media", {"media_content_id": n["media_content_id"], "media_content_type": n["media_content_type"][:100]})
            except Exception:
                # Basic media controls remain available when browsing is unsupported.
                pass
        self.choices[entity] = found
        self.browsed_at[entity] = time.monotonic()

    def sync(self, force=False):
        with self.sync_lock:
            self._sync(force)

    def _sync(self, force=False):
        c = self.connector
        with c.lock:
            states = {key: dict(value) for key, value in c.current.items() if key in self.selected and key in c.allowlist}
            connected = c.stream_connected
        rows = []
        if connected:
            for entity, state in states.items():
                rows.append(playback(state, self.choices.get(entity, {})))
        payload = {"players": rows, "radio": self.radio.catalogue() if connected else {"status": "unavailable", "country": "AU", "stations": []}}
        if c.stopping():
            return
        if force or payload != self.last_payload or time.monotonic() >= self.next_sync:
            c.cloud("media-sync", payload)
            self.last_payload = payload
            self.next_sync = time.monotonic() + 10

    def execute(self, claim):
        c = self.connector
        token = {"command_id": claim["id"], "lease_id": claim["lease_id"]}
        authorised = c.cloud("media-authorize", token)
        entity, action, value = authorised.get("entity_id"), authorised.get("action"), authorised.get("value")
        if not authorised.get("authorised") or entity not in self.selected or entity not in c.allowlist or c.stopping():
            return
        with c.lock:
            current = c.current.get(entity, {})
        caps = playback(current, self.choices.get(entity, {})) if current else {"features": []}
        status = "failed"
        service = None
        data = {"entity_id": entity}
        if action in caps["features"]:
            if action in ("play", "pause") and value is None:
                service = "media_play" if action == "play" else "media_pause"
            elif action == "volume" and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1:
                service = "volume_set"
                data["volume_level"] = value
            elif action == "favourite" and isinstance(value, str):
                target = self.choices.get(entity, {}).get(value)
                if target:
                    service = target["action"]
                    data.update(target["value"])
            elif action == "radio" and isinstance(value, str):
                target = self.radio.resolve(value)
                if target:
                    service = "play_media"
                    data.update(target)
        if service in ("media_play", "media_pause", "volume_set", "play_media", "select_source"):
            try:
                c.ha("services/media_player/" + service, data)
                status = "reported"
                # Confirm only a fresh read from HA, never an optimistic state.
                observed = c.ha("states/" + entity)
                c.accept_state(observed)
                attrs = observed.get("attributes", {})
                matches = (action == "play" and observed.get("state") == "playing" or action == "pause" and observed.get("state") == "paused"
                           or action == "volume" and isinstance(attrs.get("volume_level"), (int, float)) and abs(attrs["volume_level"] - value) <= .015)
                if matches and attrs.get("assumed_state") is not True:
                    status = "confirmed"
            except Exception:
                # A timeout can follow an accepted command. It must never be replayed.
                status = "unknown"
        c.cloud("media-ack", {**token, "status": status})
        self.wake.set()

    def publish_loop(self):
        c = self.connector
        delay = .5
        while not c.stopping():
            if not c.ready.wait(.2):
                continue
            self.wake.clear()
            try:
                self.sync()
                delay = .5
            except Exception:
                logging.warning("Media readings are waiting for a connection; home readings continue.")
                delay = min(max(delay * 2, 2), 30)
            # HA changes wake this lane; neither commands nor favourite browsing block it.
            if delay == .5:
                # Bound bursts while retaining an event wake after each upload.
                c.halt.wait(.25)
                self.wake.wait(delay)
            else:
                c.halt.wait(delay)

    def browse_loop(self):
        c = self.connector
        while not c.stopping():
            if not c.ready.wait(.2):
                continue
            with c.lock:
                states = {key: dict(value) for key, value in c.current.items()
                          if key in self.selected and key in c.allowlist and c.stream_connected}
            for entity, state in states.items():
                if c.stopping():
                    return
                if time.monotonic() - self.browsed_at.get(entity, -1000) > 180:
                    try:
                        self.browse(entity, state)
                        self.wake.set()
                    except Exception:
                        # Optional discovery cannot take down the playback publisher.
                        self.browsed_at[entity] = time.monotonic() - 170
            c.halt.wait(1)

    def run(self):
        c = self.connector
        if not self.selected:
            while not c.stopping():
                if c.ready.wait(.2):
                    try:
                        self.sync(force=True)
                    except Exception:
                        pass
                    return
            return
        self.radio.start()
        publisher = threading.Thread(target=self.publish_loop, name="homekind-media-readings", daemon=True)
        browser = threading.Thread(target=self.browse_loop, name="homekind-media-favourites", daemon=True)
        publisher.start()
        browser.start()
        delay = .5
        try:
            while not c.stopping():
                if not c.ready.wait(.2):
                    continue
                try:
                    claims = c.cloud("media-poll").get("commands", [])
                    for claim in claims[:3]:
                        if c.stopping():
                            return
                        self.execute(claim)
                    delay = .5
                except Exception:
                    logging.warning("Media controls are waiting for a connection; home readings continue.")
                    delay = min(max(delay * 2, 2), 30)
                c.halt.wait(delay)
        finally:
            self.wake.set()
            publisher.join(timeout=2)
            browser.join(timeout=2)
