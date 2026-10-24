"""Capability-negotiated bounded connector adapter; source-only rollout.

Activation requires both individual/group server fences and negotiated routes.
The journal records intent before authorisation. A restart may resend a receipt,
but can never repeat a physical action, including after an uncertain response.
"""
import copy
import fcntl
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time

from light_group import PROTOCOL, EXTENDED_PROTOCOL, VOICE_PROTOCOL, GroupRejected, _command, execute_light_group, group_action

CLOUD_URL = "https://saixlksmjzbakopvbsqv.supabase.co/functions/v1/homekind-light-group"
VOICE_CLOUD_URL = "https://saixlksmjzbakopvbsqv.supabase.co/functions/v1/homekind-voice-light"
MEMBER_RESULTS = {"confirmed", "reported", "unconfirmed", "unknown"}


class LightGroupAdapter:
    protocol = EXTENDED_PROTOCOL
    journal_name = "light-group-actions.json"
    lock_name = ".light-group.lock"

    def __init__(self, bridge, *, monotonic=time.monotonic):
        self.bridge = bridge
        self.monotonic = monotonic
        self.generation = None
        self.path = Path(bridge.directory) / self.journal_name

    def _cloud(self, operation, payload):
        credentials = self.bridge.credentials
        return self.bridge.request(CLOUD_URL + "/group-" + operation,
                                   credentials["token"],
                                   {"bridge_id": credentials["bridge_id"], **payload},
                                   timeout=2)

    def advertise(self):
        """Optional failure must not stop legacy readings or controls."""
        self.generation = None
        try:
            response = self._cloud("advertise", {"protocol": self.protocol})
            if not isinstance(response, dict) or response.get("protocol") != self.protocol or type(response.get("generation")) is not int or response["generation"] < 1:
                return False
            self.generation = response["generation"]
            return True
        except Exception:
            return False

    def _load(self):
        if not self.path.exists():
            return []
        if self.path.stat().st_size > 524288:
            raise GroupRejected("Group journal needs attention.")
        records = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(records, list) or len(records) > 64:
            raise GroupRejected("Group journal needs attention.")
        ids = set()
        for record in records:
            if not isinstance(record, dict) or record.get("stage") not in ("intent", "result", "acked"):
                raise GroupRejected("Invalid group journal.")
            command = _command(record.get("command"))
            if command["protocol"] not in ((PROTOCOL, EXTENDED_PROTOCOL) if self.protocol == EXTENDED_PROTOCOL else (self.protocol,)):
                raise GroupRejected("Unexpected journal protocol.")
            if command["id"] in ids or not isinstance(record.get("bridge_id"), str) or type(record.get("generation")) is not int:
                raise GroupRejected("Invalid group journal.")
            ids.add(command["id"])
            if record["stage"] != "intent" and (not isinstance(record.get("members"), dict) or set(record["members"]) != set(command["entities"]) or any(s not in MEMBER_RESULTS for s in record["members"].values())):
                raise GroupRejected("Invalid group receipt.")
        return records

    def _save(self, records):
        data = json.dumps(records, separators=(",", ":")).encode("utf-8")
        if len(data) > 524288 or len(records) > 64:
            raise GroupRejected("Group journal is full.")
        descriptor, temporary = tempfile.mkstemp(prefix=".light-group-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def run_once(self):
        if self.generation is None or self.bridge.stopping() or self.bridge.read_only:
            return {"status": "inactive"}
        lock = os.open(self.path.parent / self.lock_name, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"status": "busy"}
            return self._run_locked()
        finally:
            os.close(lock)

    def _run_locked(self):
        bridge = self.bridge
        credentials = dict(bridge.credentials)
        generation = self.generation
        records = self._load()  # Corruption disables only this optional feature.

        def stopped():
            return bridge.stopping() or bridge.read_only or bridge.credentials != credentials or self.generation != generation

        def enabled(entity):
            return not stopped() and entity in bridge.allowlist and any(d.get("entity_id") == entity and d.get("control_enabled") is True for d in bridge.tracked)

        def receipt(record):
            if stopped() or record["bridge_id"] != credentials["bridge_id"] or record["generation"] != generation:
                return False
            command = record["command"]
            response = self._cloud("ack", {"command_id": command["id"], "lease_id": command["lease_id"], "members": record["members"]})
            if not isinstance(response, dict) or response.get("id") != command["id"] or response.get("status") not in ("confirmed", "reported", "unconfirmed", "unknown", "expired", "cancelled"):
                raise GroupRejected("Invalid group acknowledgement.")
            record["stage"] = "acked"
            self._save(records)
            return True

        for record in records:
            if record["stage"] == "result" and record["command"]["expires_at"] > bridge.clock():
                try:
                    receipt(record)
                except Exception:
                    return {"status": "receipt-pending"}
        if stopped():
            return {"status": "inactive"}
        # Expired leases cannot be re-authorised by the backend. Retain recent
        # evidence; prune only ended records when capacity is actually needed.
        if len(records) >= 64:
            records = [r for r in records if r["command"]["expires_at"] > bridge.clock() - 86400]
            if len(records) >= 64:
                raise GroupRejected("Group journal is full.")
        response = self._cloud("poll", {})
        if not isinstance(response, dict) or "command" not in response:
            raise GroupRejected("Invalid group poll.")
        claim = response["command"]
        if claim is None:
            return {"status": "idle"}
        command = _command(claim)
        if command["protocol"] not in ((PROTOCOL, EXTENDED_PROTOCOL) if self.protocol == EXTENDED_PROTOCOL else (self.protocol,)) or claim.get("status") != "leased" or not bridge.clock() < command["expires_at"] <= bridge.clock() + 31:
            raise GroupRejected("Group lease ended.")
        if any(r["command"]["id"] == command["id"] for r in records):
            return {"status": "already-recorded"}
        scope = {**command, "entities": list(command["entities"])}
        record = {"command": scope, "bridge_id": credentials["bridge_id"], "generation": generation, "stage": "intent"}
        records.append(record)
        self._save(records)  # Must succeed before ANY authorisation/actuation.

        def read_states(entities, timeout):
            if stopped():
                raise GroupRejected("Group control ended.")
            cached = bridge.cached_states(entities) if hasattr(bridge, 'cached_states') else None
            values = list(cached.values()) if cached is not None else bridge.request(bridge.ha_url + "/states", bridge.ha_token, timeout=min(.5, timeout))
            if not isinstance(values, list):
                raise GroupRejected("Invalid local readings.")
            result = {}
            for state in values:
                if not isinstance(state, dict) or state.get("entity_id") not in entities:
                    continue
                entity = state["entity_id"]
                if entity in result:
                    raise GroupRejected("Duplicate local reading.")
                attrs = state.get("attributes") if isinstance(state.get("attributes"), dict) else {}
                context = state.get("context") if isinstance(state.get("context"), dict) else {}
                light_attrs = {k: copy.deepcopy(attrs[k]) for k in ("supported_color_modes", "brightness", "rgb_color", "hs_color", "color_mode", "color_temp_kelvin", "min_color_temp_kelvin", "max_color_temp_kelvin") if k in attrs}
                result[entity] = {"entity_id": entity, "state": state.get("state"), "last_updated": state.get("last_updated"), "context": {"id": context.get("id")}, "attributes": {**light_attrs, "assumed_state": attrs.get("assumed_state") is True}}
            return result

        started = time.monotonic()
        times = {}

        def authorise(_):
            if stopped():
                raise GroupRejected("Group control ended.")
            before = time.monotonic()
            result = self._cloud("authorise", {"command_id": scope["id"], "lease_id": scope["lease_id"]})
            times["authorize_ms"] = (time.monotonic()-before)*1000
            return result

        def dispatch(domain, service, parameters):
            expected_service, expected_fields = group_action(scope)
            if stopped() or domain != "light" or service != expected_service or parameters != expected_fields or not all(enabled(e) for e in scope["entities"]):
                raise GroupRejected("Group control changed.")
            if getattr(bridge, 'native_transport', False):
                before = time.monotonic()
                try:
                    return bridge.ha("services/light/" + service, copy.deepcopy(parameters), timeout=2)
                finally:
                    times["ha_dispatch_ms"] = (time.monotonic()-before)*1000
            return bridge.request(bridge.ha_url + "/services/light/" + service, bridge.ha_token, copy.deepcopy(parameters), timeout=2)

        try:
            result = execute_light_group(scope, read_states=read_states, locally_enabled=enabled, authorise=authorise, dispatch=dispatch, stopped=stopped, clock=self.monotonic, wall_clock=bridge.clock, sleep=bridge.sleep)
        except Exception:
            result = {"status": "unknown", "members": {e: "unknown" for e in scope["entities"]}}
        record.update(stage="result", members=result["members"])
        if hasattr(bridge, "timing"):
            bridge.timing("group", scope, {**times, "connector_total_ms": (time.monotonic()-started)*1000})
        self._save(records)  # On failure retained on-disk intent still forbids replay.
        try:
            receipt(record)
        except Exception:
            return {"status": "receipt-pending"}
        return {"status": result["status"]}


class VoiceLightAdapter(LightGroupAdapter):
    """Separate room-voice lane; same one-call executor and no-replay journal.

    Not started by the live bridge. Installation ID comes from the connector's
    paired local credentials, never a cloud-supplied actor or arbitrary URL.
    """
    protocol = VOICE_PROTOCOL
    journal_name = "voice-light-actions.json"
    lock_name = ".voice-light.lock"

    def _cloud(self, operation, payload):
        credentials = self.bridge.credentials
        return self.bridge.request(VOICE_CLOUD_URL + "/voice-light-" + operation,
                                   credentials["token"],
                                   {"bridge_id": credentials["bridge_id"],
                                    "installation_id": credentials["installation_id"], **payload},
                                   timeout=2)


class LightGroupWorker(threading.Thread):
    """Optional lane; unsupported backend never interrupts legacy workers."""
    def __init__(self, bridge):
        super().__init__(name="homekind-light-groups", daemon=True)
        self.bridge = bridge
        self.adapter = LightGroupAdapter(bridge)

    def run(self):
        next_advertisement = 0
        while not self.bridge.stopping():
            if not self.bridge.ready.wait(.1):
                continue
            if self.bridge.stopping():
                return
            delay = .5
            try:
                if self.bridge.clock() >= next_advertisement:
                    supported = self.adapter.advertise()
                    # Capability leases expire after 90 seconds on the server.
                    # Keep renewal independent of the command push connection.
                    next_advertisement = self.bridge.clock() + 30
                    if not supported:
                        delay = 5
                if self.adapter.generation is not None:
                    self.adapter.run_once()
            except Exception:
                # No action/authorisation retry: journal and one-use server lease
                # retain uncertainty. This lane alone waits for renegotiation.
                self.adapter.generation = None
                next_advertisement = self.bridge.clock() + 5
                delay = 5
                logging.warning("Coordinated light control unavailable; existing home workers continue.")
            if delay > .5:
                self.bridge.halt.wait(delay)
            else:
                self.bridge.wait_commands('group') if hasattr(self.bridge, 'wait_commands') else self.bridge.halt.wait(delay)
