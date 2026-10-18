import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "homekind_bridge"))
import test_light_group_adapter as fixtures
from light_group import VOICE_PROTOCOL, GroupRejected
from light_group_adapter import VoiceLightAdapter, LightGroupAdapter


class VoiceAdapterTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.AdapterTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.claim["protocol"] = VOICE_PROTOCOL
        self.f.claim["binding_id"] = self.f.claim.pop("group_id")
        self.f.bridge.credentials["installation_id"] = "00000000-0000-4000-8000-000000000005"
        self.f.adapter = VoiceLightAdapter(self.f.bridge, monotonic=lambda: self.f.now)
        original = self.f.bridge.request

        def request(url, token, data=None, timeout=12):
            if "/voice-light-" in url:
                self.assertEqual(data["installation_id"], self.f.bridge.credentials["installation_id"])
                response = original(url.replace("/voice-light-", "/group-"), token, data, timeout)
                if url.endswith("voice-light-advertise"):
                    response["protocol"] = VOICE_PROTOCOL
                return response
            return original(url, token, data, timeout)
        self.f.bridge.request = request

    def start(self):
        return self.f.start()

    def test_one_call_for_every_selected_light_and_separate_private_journal(self):
        self.assertEqual(self.start()["status"], "confirmed")
        self.assertEqual((self.f.auth, self.f.sent), (1, 1))
        self.assertEqual(self.f.adapter.path.name, "voice-light-actions.json")
        self.assertNotEqual(self.f.adapter.path, LightGroupAdapter(self.f.bridge).path)
        self.assertNotIn("secret", self.f.adapter.path.read_text())

    def test_single_room_light_uses_identical_one_call_executor(self):
        self.f.claim["entities"] = ["light.one"]
        self.assertEqual(self.start()["status"], "confirmed")
        self.assertEqual(self.f.sent, 1)

    def test_restart_never_repeats_voice_dispatch(self):
        self.start()
        self.f.adapter = VoiceLightAdapter(self.f.bridge)
        self.assertEqual(self.start()["status"], "already-recorded")
        self.assertEqual((self.f.auth, self.f.sent), (1, 1))

    def test_uncertain_dispatch_and_lost_receipt_only_replay_receipt(self):
        self.f.lost_dispatch = True
        self.f.lost_ack = True
        self.assertEqual(self.start()["status"], "receipt-pending")
        self.f.lost_ack = False
        self.f.adapter = VoiceLightAdapter(self.f.bridge)
        self.start()
        self.assertEqual(self.f.sent, 1)
        self.assertEqual(set(json.loads(self.f.adapter.path.read_text())[0]["members"].values()), {"unknown"})

    def test_local_selection_or_credentials_change_stops_dispatch(self):
        self.f.after_auth = lambda: self.f.bridge.allowlist.clear()
        self.start()
        self.assertEqual((self.f.auth, self.f.sent), (1, 0))

    def test_fsync_failure_prevents_authorisation_and_any_device_call(self):
        self.f.adapter.advertise()
        with patch.object(self.f.adapter, "_save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.f.adapter.run_once()
        self.assertEqual((self.f.auth, self.f.sent), (0, 0))

    def test_cross_protocol_poll_is_rejected_before_journalling(self):
        self.f.claim["protocol"] = "homekind.light-group.v1"
        self.f.claim["group_id"] = self.f.claim.pop("binding_id")
        with self.assertRaises(GroupRejected):
            self.start()
        self.assertFalse(self.f.adapter.path.exists())
        self.assertEqual(self.f.sent, 0)

    def test_voice_lane_cannot_switch_off_or_accept_non_light_targets(self):
        self.f.claim["desired"] = "off"
        with self.assertRaises(GroupRejected):
            self.start()
        self.assertEqual(self.f.sent, 0)


if __name__ == "__main__":
    unittest.main()
