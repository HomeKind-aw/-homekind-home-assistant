import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

APP = Path(__file__).parents[1] / "homekind_bridge"
sys.path.insert(0, str(APP))
import managed_settings as settings
from setup_server import SetupManager, SetupError
from bridge import atomic_json, BridgeError


class ManagedSettingsTests(unittest.TestCase):
    def options(self):
        return settings.normalise({"managed_settings": True, "management_id": "a" * 32,
            "entity_ids": ["sensor.temperature", "light.room", "camera.front", "media_player.sonos"],
            "read_only": True})

    def response(self, **changes):
        return {"protocol": 1, "management_id": "a" * 32, "revision": 1,
                "config": {"read_only": False, "live_camera_entity_ids": ["camera.front"], "media_control_entity_ids": ["media_player.sonos"]}, **changes}

    def test_legacy_upgrade_preserves_approved_features_without_enrolling_management(self):
        old = {"entity_ids": ["light.room", "camera.front"], "read_only": False, "live_camera_entity_ids": ["camera.front"], "pairing_code": ""}
        migrated = settings.normalise(old)
        self.assertFalse(migrated["read_only"])
        self.assertEqual(migrated["entity_ids"], old["entity_ids"])
        self.assertEqual(migrated["live_camera_entity_ids"], old["live_camera_entity_ids"])
        self.assertFalse(migrated["managed_settings"])
        self.assertEqual(migrated["media_control_entity_ids"], [])
        self.assertEqual(old, {k: migrated[k] for k in old})

    def test_valid_features_apply_without_altering_pairing_or_entity_selection(self):
        original = self.options()
        saved = settings.apply(original, self.response())
        self.assertFalse(saved["read_only"])
        self.assertEqual(saved["applied_revision"], 1)
        for key in ("entity_ids", "pairing_code", "management_id", "managed_settings"):
            self.assertEqual(saved[key], original[key])
        self.assertEqual(settings.normalise(saved), saved)

    def test_replayed_and_older_requests_cannot_overwrite_newer_choices(self):
        current = {**self.options(), "applied_revision": 2}
        self.assertIsNone(settings.apply(current, self.response(revision=1)))
        self.assertIsNone(settings.apply(current, self.response(revision=2)))

    def test_wrong_enrolment_and_unknown_protocol_are_refused(self):
        for changes in ({"management_id": "b" * 32}, {"protocol": 2}, {"protocol": True}, {"revision": True}, {"revision": 0}):
            with self.assertRaises(settings.SettingsError):
                settings.apply(self.options(), self.response(**changes))

    def test_cloud_cannot_enrol_management_or_change_selected_entities(self):
        for key, value in (("entity_ids", ["camera.private"]), ("managed_settings", True), ("ha_url", "https://invalid.example")):
            response = self.response()
            response["config"][key] = value
            with self.assertRaises(settings.SettingsError):
                settings.apply(self.options(), response)
        self.assertIsNone(settings.apply(settings.normalise({}), self.response()))

    def test_device_boundaries_domains_duplicates_and_types_are_checked_locally(self):
        for field, value in (("live_camera_entity_ids", ["camera.private"]), ("live_camera_entity_ids", ["light.room"]),
                             ("media_control_entity_ids", ["media_player.sonos"] * 2), ("read_only", "false")):
            response = self.response()
            response["config"][field] = value
            with self.assertRaises(settings.SettingsError):
                settings.apply(self.options(), response)

    def test_unknown_saved_schema_is_preserved_and_refused(self):
        with self.assertRaises(settings.SettingsError):
            settings.normalise({"settings_version": 2})

    def test_settings_exchange_persists_before_acknowledging_and_keeps_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = SetupManager(directory, str(Path(directory) / "options.json"))
            try:
                atomic_json(manager.settings, self.options())
                atomic_json(manager.credentials, {"bridge_id": "test-bridge", "token": "c" * 64})
                before = manager.credentials.read_bytes()
                reports = []
                def request(url, token, data, **kwargs):
                    reports.append(data)
                    return {"desired": self.response()}
                manager.request = request
                manager.configuration_once()
                self.assertTrue(manager.changed.is_set())
                self.assertEqual(manager.options()["applied_revision"], 1)
                self.assertEqual(reports[0]["applied_revision"], 0)
                manager.configuration_once()
                self.assertEqual(reports[1]["applied_revision"], 1)
                self.assertEqual(manager.credentials.read_bytes(), before)
            finally:
                manager.stopped.set()

    def test_concurrent_local_revocation_wins_over_in_flight_cloud_response(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = SetupManager(directory, str(Path(directory) / "options.json"))
            try:
                atomic_json(manager.settings, self.options())
                atomic_json(manager.credentials, {"bridge_id": "test-bridge", "token": "c" * 64})
                def request(*args, **kwargs):
                    atomic_json(manager.settings, {**self.options(), "managed_settings": False, "management_id": ""})
                    return {"desired": self.response()}
                manager.request = request
                manager.configuration_once()
                self.assertFalse(manager.options()["managed_settings"])
                self.assertTrue(manager.options()["read_only"])
                self.assertFalse(manager.changed.is_set())
            finally:
                manager.stopped.set()

    def test_failed_atomic_save_never_acknowledges_a_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = SetupManager(directory, str(Path(directory) / "options.json"))
            try:
                atomic_json(manager.settings, self.options())
                atomic_json(manager.credentials, {"bridge_id": "test-bridge", "token": "c" * 64})
                manager.request = lambda *args, **kwargs: {"desired": self.response()}
                with patch("setup_server.atomic_json", side_effect=OSError("synthetic disk failure")):
                    with self.assertRaises(OSError):
                        manager.configuration_once()
                self.assertEqual(manager.options()["applied_revision"], 0)
                self.assertFalse(manager.changed.is_set())
            finally:
                manager.stopped.set()

    def test_local_settings_changes_invalidate_pending_remote_choices(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SUPERVISOR_TOKEN": "synthetic-local-token"}):
            states = [{"entity_id": entity, "state": "off", "attributes": {"device_class": "temperature"} if entity.startswith("sensor.") else {}} for entity in self.options()["entity_ids"]]
            manager = SetupManager(directory, str(Path(directory) / "options.json"), lambda *args, **kwargs: states)
            try:
                atomic_json(manager.settings, self.options())
                atomic_json(manager.credentials, {"bridge_id": "test-bridge", "token": "c" * 64})
                manager.configure({"entity_ids": self.options()["entity_ids"], "read_only": False})
                self.assertNotEqual(manager.options()["management_id"], "a" * 32)
                with self.assertRaises(settings.SettingsError):
                    settings.apply(manager.options(), self.response())
            finally:
                manager.stopped.set()
