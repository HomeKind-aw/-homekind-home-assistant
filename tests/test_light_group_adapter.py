import copy
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "homekind_bridge"))
from light_group import PROTOCOL, GroupRejected
from light_group_adapter import LightGroupAdapter, LightGroupWorker


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.claim = {"protocol": PROTOCOL, "id": "00000000-0000-4000-8000-000000000001", "lease_id": "00000000-0000-4000-8000-000000000002", "household_id": "00000000-0000-4000-8000-000000000003", "group_id": "00000000-0000-4000-8000-000000000004", "revision": 1, "entities": ["light.one", "light.two"], "desired": "on", "expires_at": 120, "status": "leased"}
        self.calls = []
        self.sent = 0
        self.auth = 0
        self.now = 100
        self.lost_ack = False
        self.lost_dispatch = False
        self.after_auth = lambda: None
        self.bridge = SimpleNamespace(directory=Path(self.directory.name), credentials={"bridge_id": "bridge", "token": "a" * 64}, read_only=False, stopping=lambda: False, allowlist=set(self.claim["entities"]), tracked=[{"entity_id": e, "control_enabled": True} for e in self.claim["entities"]], clock=lambda: self.now, sleep=self.sleep, ha_url="http://supervisor/core/api", ha_token="local-secret", request=self.request)
        self.adapter = LightGroupAdapter(self.bridge, monotonic=lambda: self.now)

    def sleep(self, amount):
        self.now += amount

    def request(self, url, token, data=None, timeout=12):
        self.calls.append((url, token, copy.deepcopy(data), timeout))
        if url.endswith("group-advertise"):
            return {"protocol": data["protocol"], "generation": 2}
        if url.endswith("group-poll"):
            return {"command": copy.deepcopy(self.claim)}
        if url.endswith("group-authorise"):
            self.auth += 1
            self.assertEqual(json.loads(self.adapter.path.read_text())[0]["stage"], "intent")
            self.after_auth()
            return {**self.claim, "status": "dispatched"}
        if url.endswith("group-ack"):
            if self.lost_ack:
                raise TimeoutError()
            return {"id": self.claim["id"], "status": "confirmed"}
        if url.endswith("/states"):
            return [{"entity_id": e, "state": "on" if self.sent else "off", "last_updated": str(self.sent), "context": {"id": str(self.sent)}, "attributes": {"private": "never send"}} for e in self.claim["entities"]] + [{"entity_id": "sensor.private", "state": "private"}]
        if url.endswith("/services/light/turn_on"):
            self.sent += 1
            self.assertEqual(data, {"entity_id": self.claim["entities"]})
            if self.lost_dispatch:
                raise TimeoutError()
            return []
        self.fail("Unexpected route: " + url)

    def start(self):
        self.assertTrue(self.adapter.advertise())
        return self.adapter.run_once()

    def test_exact_single_dispatch_bounded_timeouts_and_private_journal(self):
        self.assertEqual(self.start()["status"], "confirmed")
        self.assertEqual((self.sent, self.auth), (1, 1))
        self.assertEqual(os.stat(self.adapter.path).st_mode & 0o777, 0o600)
        text = self.adapter.path.read_text()
        self.assertNotIn("secret", text)
        self.assertNotIn("a" * 64, text)
        self.assertNotIn("private", text)
        self.assertTrue(all(0 < call[3] <= 2 for call in self.calls))
        ack = next(call[2] for call in self.calls if call[0].endswith("group-ack"))
        self.assertEqual(set(ack), {"bridge_id", "command_id", "lease_id", "members"})

    def test_restart_and_duplicate_poll_never_reauthorise_or_repeat_dispatch(self):
        self.start()
        self.adapter = LightGroupAdapter(self.bridge)
        self.assertEqual(self.start()["status"], "already-recorded")
        self.assertEqual((self.sent, self.auth), (1, 1))

    def test_lost_receipt_retries_only_identical_receipt_after_restart(self):
        self.lost_ack = True
        self.assertEqual(self.start()["status"], "receipt-pending")
        self.lost_ack = False
        self.adapter = LightGroupAdapter(self.bridge)
        self.start()
        acks = [c[2] for c in self.calls if c[0].endswith("group-ack")]
        self.assertEqual(acks[0], acks[1])
        self.assertEqual((self.sent, self.auth), (1, 1))

    def test_uncertain_dispatch_never_retried_and_every_member_unknown(self):
        self.lost_dispatch = True
        self.assertEqual(self.start()["status"], "unknown")
        self.adapter.run_once()
        self.assertEqual(self.sent, 1)
        self.assertEqual(set(json.loads(self.adapter.path.read_text())[0]["members"].values()), {"unknown"})

    def test_read_only_or_unnegotiated_is_inactive(self):
        self.assertEqual(self.adapter.run_once()["status"], "inactive")
        self.adapter.advertise()
        self.bridge.read_only = True
        self.assertEqual(self.adapter.run_once()["status"], "inactive")
        self.assertEqual(self.sent, 0)

    def test_local_opt_out_during_authorisation_prevents_dispatch(self):
        self.after_auth = lambda: self.bridge.allowlist.clear()
        self.start()
        self.assertEqual((self.auth, self.sent), (1, 0))

    def test_credentials_change_during_authorisation_prevents_dispatch(self):
        self.after_auth = lambda: self.bridge.credentials.update(token="b" * 64)
        self.start()
        self.assertEqual(self.sent, 0)
        self.assertFalse(any(c[0].endswith("group-ack") for c in self.calls))

    def test_failed_intent_save_prevents_authorisation(self):
        self.adapter.advertise()
        with patch.object(self.adapter, "_save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.adapter.run_once()
        self.assertEqual((self.auth, self.sent), (0, 0))

    def test_corrupt_journal_fails_closed_before_poll(self):
        self.adapter.path.write_text("corrupt")
        self.adapter.advertise()
        with self.assertRaises(ValueError):
            self.adapter.run_once()
        self.assertFalse(any(c[0].endswith("group-poll") for c in self.calls))

    def test_uncertain_authorisation_keeps_intent_and_never_dispatches(self):
        original = self.bridge.request
        def uncertain(url, *args, **kwargs):
            if url.endswith("group-authorise"):
                self.auth += 1
                raise TimeoutError()
            return original(url, *args, **kwargs)
        self.bridge.request = uncertain
        self.start()
        self.adapter.run_once()
        self.assertEqual((self.auth, self.sent), (1, 0))

    def test_failed_result_save_keeps_intent_to_prevent_restart_replay(self):
        original = self.adapter._save
        def save(records):
            if records[-1]["stage"] == "result":
                raise OSError("disk full")
            return original(records)
        with patch.object(self.adapter, "_save", side_effect=save):
            with self.assertRaises(OSError):
                self.start()
        self.assertEqual(json.loads(self.adapter.path.read_text())[0]["stage"], "intent")
        self.adapter = LightGroupAdapter(self.bridge)
        self.start()
        self.assertEqual((self.auth, self.sent), (1, 1))

    def test_expired_or_duplicate_scope_rejected_without_physical_action(self):
        self.claim["expires_at"] = 99
        with self.assertRaises(GroupRejected):
            self.start()
        self.assertEqual(self.sent, 0)
        self.claim["expires_at"] = 120
        self.claim["entities"] = ["light.one", "light.one"]
        with self.assertRaises(GroupRejected):
            self.adapter.run_once()

    def test_unsupported_advertisement_does_not_raise_into_legacy_worker(self):
        self.bridge.request = lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError())
        self.assertFalse(self.adapter.advertise())
        self.assertEqual(self.adapter.run_once()["status"], "inactive")

    def test_second_local_adapter_cannot_race_journal(self):
        self.adapter.advertise()
        with open(Path(self.directory.name) / ".light-group.lock", "w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.adapter.run_once()["status"], "busy")
        self.assertEqual(self.sent, 0)

    def test_optional_worker_stops_cleanly_when_backend_is_unsupported(self):
        halt = threading.Event()
        ready = threading.Event()
        ready.set()
        self.bridge.halt, self.bridge.ready = halt, ready
        self.bridge.stopping = halt.is_set
        def unavailable(*args, **kwargs):
            halt.set()
            raise TimeoutError()
        self.bridge.request = unavailable
        worker = LightGroupWorker(self.bridge)
        worker.start()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertIsNone(worker.adapter.generation)
        self.assertEqual(self.sent, 0)

    def test_worker_waits_for_current_readings_before_negotiation(self):
        halt, ready = threading.Event(), threading.Event()
        self.bridge.halt, self.bridge.ready = halt, ready
        self.bridge.stopping = halt.is_set
        worker = LightGroupWorker(self.bridge)
        worker.start()
        halt.set()
        ready.set()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.calls, [])

    def test_negotiated_worker_runs_exact_group_then_stops_without_replay(self):
        halt, ready = threading.Event(), threading.Event()
        ready.set()
        self.bridge.halt, self.bridge.ready = halt, ready
        self.bridge.stopping = halt.is_set
        original = self.bridge.request
        def request(url, *args, **kwargs):
            result = original(url, *args, **kwargs)
            if url.endswith('group-ack'):
                halt.set()
            return result
        self.bridge.request = request
        worker = LightGroupWorker(self.bridge)
        # Assertion in synthetic authoriser inspects the same durable path.
        self.adapter = worker.adapter
        worker.start()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual((self.auth, self.sent), (1, 1))


if __name__ == "__main__":
    unittest.main()
