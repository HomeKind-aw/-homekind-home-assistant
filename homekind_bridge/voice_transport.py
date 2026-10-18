"""Bounded paired voice exchange and metadata collector; no microphone/audio API.

The caller supplies events only from its authenticated Home Assistant stream.
This candidate is not started by Connector.run until the matched voice rollout.
"""
import copy
import json
import re
import threading
from collections import deque

from pathlib import Path

from voice_events import (UUID, INTENTS, FEEDBACK, VoiceError, VoiceOutbox, event_from_ha,
                          validate_binding, validate_event, timestamp)

VOICE_URL = "https://saixlksmjzbakopvbsqv.supabase.co/functions/v1/homekind-voice"
BINDING_KEYS = ("protocol", "bridge_id", "installation_id", "endpoint_id",
                "binding_id", "revision", "enabled", "muted", "consent_current",
                "expires_at", "intents")


class VoiceCancelled(OSError):
    """Local shutdown is unavailable transport, never evidence of revocation."""


class VoiceCloud:
    def __init__(self, bridge, *, access_error=PermissionError, cancelled=lambda: False):
        self.bridge = bridge
        self.access_error = access_error
        self.cancelled = cancelled
        self.credentials = {k: bridge.credentials.get(k) for k in ("bridge_id", "installation_id", "token")}
        if any(not isinstance(self.credentials[k], str) or not UUID.fullmatch(self.credentials[k])
               for k in ("bridge_id", "installation_id")) or not isinstance(self.credentials["token"], str) or not re.fullmatch(r"[a-f0-9]{64}", self.credentials["token"]):
            raise VoiceError("Check the paired voice connection.")
        self.scope = {k: self.credentials[k] for k in ("bridge_id", "installation_id")}

    def current(self):
        if self.cancelled() or self.bridge.stopping():
            raise VoiceCancelled("The voice worker stopped.")
        if any(self.bridge.credentials.get(k) != v for k, v in self.credentials.items()):
            raise VoiceError("The voice connection changed.")

    def _post(self, operation, payload):
        fields = {"snapshot": set(), "dispatch": {"revision", "event"},
                  "receipt": {"event_id", "binding_id", "revision"}}
        if operation not in fields or not isinstance(payload, dict) or set(payload) != fields[operation]:
            raise VoiceError("Unsupported voice operation.")
        self.current()
        body = {**self.scope, **payload}
        if len(json.dumps(body, separators=(",", ":")).encode()) > 8192:
            raise VoiceError("Voice metadata is too large.")
        try:
            response = self.bridge.request(VOICE_URL + "/voice-" + operation,
                                           self.credentials["token"], body, timeout=2)
        except self.access_error:
            raise VoiceError("Voice connection access ended.") from None
        except Exception:
            # Shared connector errors never leak endpoint/credential/provider data.
            raise OSError("Voice exchange could not be checked.") from None
        self.current()
        try:
            if len(json.dumps(response, separators=(",", ":")).encode()) > 131072:
                raise ValueError()
        except (TypeError, ValueError, OverflowError):
            raise OSError("Voice exchange could not be checked.") from None
        return response

    def snapshot(self):
        response = self._post("snapshot", {})
        try:
            if not isinstance(response, dict) or type(response.get("protocol")) is not int or response["protocol"] != 1 or any(response.get(k) != v for k, v in self.scope.items()):
                raise ValueError()
            rows = response.get("bindings")
            if not isinstance(rows, list) or len(rows) > 100:
                raise ValueError()
            bindings, identities, endpoints = [], set(), set()
            now = self.bridge.clock()
            for value in rows:
                if not isinstance(value, dict) or any(k not in value for k in BINDING_KEYS):
                    raise ValueError()
                b = {k: copy.deepcopy(value[k]) for k in BINDING_KEYS}
                if type(b["protocol"]) is not int or b["protocol"] != 1 or any(b[k] != v for k, v in self.scope.items()) or any(not isinstance(b[k], str) or not UUID.fullmatch(b[k]) for k in ("binding_id", "endpoint_id")):
                    raise ValueError()
                if type(b["revision"]) is not int or not 1 <= b["revision"] <= 999999999 or any(type(b[k]) is not bool for k in ("enabled", "muted", "consent_current")):
                    raise ValueError()
                if not now < timestamp(b["expires_at"]) <= now + 16 or not isinstance(b["intents"], list) or not 1 <= len(b["intents"]) <= 3 or any(not isinstance(x, str) or x not in INTENTS for x in b["intents"]) or len(set(b["intents"])) != len(b["intents"]):
                    raise ValueError()
                active = b["enabled"] and not b["muted"] and b["consent_current"]
                if b["binding_id"] in identities or active and b["endpoint_id"] in endpoints:
                    raise ValueError()
                identities.add(b["binding_id"])
                if active:
                    endpoints.add(b["endpoint_id"])
                bindings.append(b)
            return bindings
        except (ValueError, TypeError, KeyError):
            # Malformed/unavailable setup is not evidence that consent was revoked.
            raise OSError("Voice setup could not be checked.") from None

    def binding(self, endpoint):
        candidates = [b for b in self.snapshot() if b["endpoint_id"] == endpoint and b["enabled"] and not b["muted"] and b["consent_current"]]
        if len(candidates) != 1:
            raise VoiceError("This endpoint has no current accepted voice choice.")
        return candidates[0]

    def dispatch(self, event, revision):
        event = validate_event(event, self.bridge.clock())
        if type(revision) is not int or not 1 <= revision <= 999999999:
            raise VoiceError("Check the voice revision.")
        return self._post("dispatch", {"revision": revision, "event": event})

    def receipt(self, event_id, binding_id, revision):
        if any(not isinstance(x, str) or not UUID.fullmatch(x) for x in (event_id, binding_id)) or type(revision) is not int or not 1 <= revision <= 999999999:
            raise VoiceError("Check the voice receipt scope.")
        return self._post("receipt", {"event_id": event_id, "binding_id": binding_id, "revision": revision})


class VoiceCollector:
    """Nonblocking HA admission; cloud work runs outside the HA reader thread.

    Metadata only, bounded to20waiting events. Its process/tick methods are for a
    single optional voice worker; SQLite continues to guard dispatch/replay.
    """
    def __init__(self, cloud, outbox, feedback=lambda value: None):
        if cloud.scope != outbox.scope:
            raise VoiceError("Voice storage and connection do not match.")
        self.cloud, self.outbox, self.feedback = cloud, outbox, feedback
        self.waiting, self.guard = deque(), threading.Lock()
        self.working = threading.Lock()
        self.last_feedback = {}

    def submit(self, message):
        self.cloud.current()
        event = event_from_ha(message, self.cloud.bridge.clock())
        with self.guard:
            for existing in self.waiting:
                if existing["event_id"] == event["event_id"]:
                    if existing != event:
                        raise VoiceError("Voice event content changed.")
                    return event["event_id"]
            if len(self.waiting) >= 20:
                raise VoiceError("Voice intake is busy. Use another way to ask.")
            self.waiting.append(event)
        return event["event_id"]

    def process_one(self):
        with self.guard:
            event = dict(self.waiting[0]) if self.waiting else None
        if event is None:
            return None
        try:
            binding = self.cloud.binding(event["endpoint_id"])
            status = self.outbox.enqueue(event, binding)
        except (OSError, TimeoutError):
            return None  # Keep original ID/time; never fabricate accepted setup.
        except VoiceError:
            status = "failed"
        with self.guard:
            if self.waiting and self.waiting[0]["event_id"] == event["event_id"]:
                self.waiting.popleft()
        return {"event_id": event["event_id"], "status": status}

    def _feedback(self, result):
        if not result or result["status"] not in FEEDBACK:
            return
        record = self.outbox.feedback_record(result["event_id"])
        if not record:
            return
        event, revision = record
        try:
            binding = self.cloud.binding(event["endpoint_id"])
            if validate_binding(binding, event, self.cloud.scope, self.cloud.bridge.clock()) != revision:
                return
        except (VoiceError, OSError, TimeoutError):
            return
        ident = result["event_id"]
        if self.last_feedback.get(ident) == result["status"]:
            return
        # Mark before the callback; an uncertain local feedback must not cause
        # action replay or repeated success speech. No audio delivery is claimed.
        if len(self.last_feedback) >= 1000:
            self.last_feedback.clear()
        self.last_feedback[ident] = result["status"]
        value = {"protocol": 1, "event_id": ident, "endpoint_id": event["endpoint_id"],
                 "binding_id": event["binding_id"], "binding_revision": revision,
                 "status": result["status"], "text": FEEDBACK[result["status"]]}
        try:
            self.feedback(value)
        except Exception:
            pass  # Optional output cannot change a committed/uncertain action.

    def tick(self):
        if not self.working.acquire(blocking=False):
            return []
        try:
            results = []
            for step in (self.process_one,
                         lambda: self.outbox.exchange_one(self.cloud.dispatch, self.cloud.binding),
                         lambda: self.outbox.poll_receipt(self.cloud.receipt, self.cloud.binding)):
                result = step()
                # Recognition feedback precedes any subsequent dispatch, rather
                # than incorrectly saying "not sent" after the action commits.
                self._feedback(result)
                if result:
                    results.append(result)
            return results
        finally:
            self.working.release()


class VoiceWorker(threading.Thread):
    """Optional private candidate; not enabled by the live Connector.run.

    Owns only its voice thread, local metadata spool and cancellation signals.
    local_ready means storage opened, never that a microphone/cloud is verified.
    A timed-out stop keeps the spool locked until the actual thread finishes.
    """
    def __init__(self, bridge, directory, *, feedback=lambda value: None,
                 access_error=PermissionError):
        super().__init__(name="homekind-voice-metadata", daemon=True)
        self.bridge, self.directory = bridge, Path(directory)
        self.feedback, self.access_error = feedback, access_error
        self.halt, self.wake = threading.Event(), threading.Event()
        self.local_ready, self.finished = threading.Event(), threading.Event()
        self.collector = None
        self.state = "not_started"

    def stopping(self):
        return self.halt.is_set() or self.bridge.stopping()

    def submit(self, message):
        if self.stopping() or not self.local_ready.is_set() or self.collector is None:
            raise VoiceError("Voice intake is not ready. Use another way to ask.")
        ident = self.collector.submit(message)
        self.wake.set()
        return ident

    def stop(self, timeout=20):
        self.halt.set()
        self.wake.set()
        if self.ident is not None and threading.current_thread() is not self:
            self.join(timeout=timeout)
        return not self.is_alive()

    def run(self):
        outbox = None
        try:
            if self.stopping():
                return
            cloud = VoiceCloud(self.bridge, access_error=self.access_error,
                               cancelled=self.halt.is_set)
            outbox = VoiceOutbox(self.directory, **cloud.scope, clock=self.bridge.clock)
            self.collector = VoiceCollector(cloud, outbox, self.feedback)
            self.state = "local_ready"
            self.local_ready.set()
            delay = .5
            while not self.stopping():
                self.wake.clear()
                try:
                    cloud.current()
                    results = self.collector.tick()
                    with self.collector.guard:
                        waiting = bool(self.collector.waiting)
                    delay = min(30, max(2, delay * 2)) if waiting and not results else .5
                except VoiceError:
                    self.state = "connection_ended"
                    return
                except Exception:
                    # Optional storage/transport failures never stop other home
                    # workers and never expose event/provider/credential data.
                    delay = min(30, max(2, delay * 2))
                self.wake.wait(delay)
        except Exception:
            self.state = "unavailable"
        finally:
            self.local_ready.clear()
            # The owning thread alone closes storage after pending calls end.
            if outbox is not None:
                outbox.close()
            if self.state not in ("unavailable", "connection_ended"):
                self.state = "stopped"
            self.finished.set()
