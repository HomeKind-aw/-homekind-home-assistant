import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("voice_preflight", ROOT / "scripts/preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class SetupTests(unittest.TestCase):
    def snapshot(self, device_id="known"):
        return preflight.inspect_snapshot(
            {"version": "fixture", "components": ["esphome", "assist_pipeline", "wyoming"]},
            [{"id": "known", "name": "homekind-voice-fixture", "area_id": "lounge"}],
            [{"device_id": "known", "entity_id": "media_player.homekind"}, {"device_id": "known", "entity_id": "switch.homekind_microphone_mute"}],
            [{"stt_engine": "wyoming.local", "tts_engine": "wyoming.piper", "conversation_engine": "conversation.home_assistant"}],
            device_id,
        )

    def test_configuration_cannot_claim_physical_or_homekind_acceptance(self):
        report = self.snapshot()
        self.assertFalse(report["physical_verified"])
        checks = {c["check"]: c for c in report["checks"]}
        self.assertEqual(checks["HomeKind conversation"]["status"], "not commissioned")
        self.assertEqual(checks["Assigned device pipeline"]["status"], "manual")
        self.assertEqual(checks["Microphone and speaker"]["status"], "physical test required")

    def test_name_match_is_never_automatic_identity_or_room_binding(self):
        report = self.snapshot(None)
        self.assertEqual(report["candidate_device_ids"], ["known"])
        self.assertEqual(next(c for c in report["checks"] if c["check"] == "Room endpoint")["status"], "setup")
        report = self.snapshot("different")
        self.assertEqual(next(c for c in report["checks"] if c["check"] == "Room endpoint")["status"], "attention")

    def test_missing_pipeline_or_disabled_speaker_stays_unready(self):
        report = preflight.inspect_snapshot({}, [{"id": "known"}], [{"device_id": "known", "entity_id": "media_player.homekind", "disabled_by": "user"}], [], "known")
        checks = {c["check"]: c for c in report["checks"]}
        for key in ("Speech pipeline", "Speaker entity", "Room assignment", "Mute control"):
            self.assertEqual(checks[key]["status"], "setup")

    def test_local_http_and_remote_https_only_without_embedded_credentials(self):
        self.assertEqual(preflight.websocket_url("http://homeassistant.local:8123"), "ws://homeassistant.local:8123/api/websocket")
        self.assertTrue(preflight.websocket_url("https://ha.example.test").startswith("wss:"))
        for url in ("http://ha.example.test", "https://user:password@ha.example.test", "https://ha.example.test/?token=private", "file:///etc/passwd", "https://ha.example.test/api"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                preflight.websocket_url(url)

    def test_secret_creation_keeps_values_hidden_private_and_never_replaces_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(ROOT / "scripts/create_secrets.py"), "--directory", directory], input="Example Wi-Fi\nexample-private-password\n", text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("example-private-password", result.stdout)
            path = Path(directory) / "secrets.yaml"
            before = path.read_bytes()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            again = subprocess.run([sys.executable, str(ROOT / "scripts/build_fixture.py"), directory], capture_output=True)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
