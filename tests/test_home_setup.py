import http.client
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

APP = Path(__file__).parents[1] / "homekind_bridge"
sys.path.insert(0, str(APP))
spec = importlib.util.spec_from_file_location("homekind_setup", APP / "setup_server.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class HomeSetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environ = patch.dict(os.environ, {"SUPERVISOR_TOKEN": "test-supervisor-secret"})
        self.environ.start()
        self.requests = []
        def request(url, token, data=None):
            self.requests.append((url, token, data))
            return [
                {"entity_id": "sensor.room_temperature", "state": "21.4", "attributes": {"device_class": "temperature", "friendly_name": "Room <script>alert(1)</script>"}},
                {"entity_id": "binary_sensor.door", "state": "off", "attributes": {"device_class": "door", "friendly_name": "Front door"}},
                {"entity_id": "camera.private", "state": "streaming", "attributes": {}},
                {"entity_id": "person.resident", "state": "home", "attributes": {}},
                {"entity_id": "sensor.heart", "state": "70", "attributes": {"device_class": "heart_rate"}},
            ]
        self.manager = setup.SetupManager(self.directory.name, str(Path(self.directory.name) / "options.json"), request)
        self.server = setup.SetupServer(("127.0.0.1", 0), self.manager, ingress_ip="127.0.0.1")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.manager.stopped.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.environ.stop()
        self.directory.cleanup()

    def http(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        if isinstance(body, dict):
            body = json.dumps(body)
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        content = response.read()
        result = response.status, dict(response.getheaders()), content
        connection.close()
        return result

    def configure(self, data, csrf=None, headers=None):
        return self.http("POST", "/api/configure", data, {"Content-Type": "application/json", "X-HomeKind-Setup": csrf or self.manager.csrf, **(headers or {})})

    def test_ingress_rejects_direct_requests_even_with_spoofed_forwarded_header(self):
        self.server.ingress_ip = "172.30.32.2"
        status, _, _ = self.http("GET", "/", headers={"X-Forwarded-For": "172.30.32.2"})
        self.assertEqual(status, 403)
        self.assertFalse(self.requests)

    def test_boots_without_pairing_and_provides_a_real_setup_page(self):
        self.assertEqual(self.manager.status()["phase"], "setup")
        status, headers, body = self.http("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Your home, connected.", body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])

    def test_entity_discovery_includes_camera_metadata_but_omits_readings(self):
        status, _, body = self.http("GET", "/api/entities")
        self.assertEqual(status, 200)
        result = json.loads(body)["entities"]
        self.assertEqual({x["entity_id"] for x in result}, {"sensor.room_temperature", "binary_sensor.door", "camera.private"})
        self.assertFalse(any("state" in x or "attributes" in x for x in result))
        self.assertEqual(self.requests[0][0], "http://supervisor/core/api/states")
        self.assertEqual(self.requests[0][1], "test-supervisor-secret")
        self.assertTrue(all(request[2] is None for request in self.requests))

    def test_pairing_and_allowlist_default_to_read_only(self):
        status, _, _ = self.configure({"pairing_code": "a" * 64, "entity_ids": ["sensor.room_temperature"]})
        self.assertEqual(status, 200)
        self.assertEqual(self.manager.options(), {"read_only": True, "pairing_code": "a" * 64, "entity_ids": ["sensor.room_temperature"], "live_camera_entity_ids": [], "media_control_entity_ids": []})
        self.assertEqual(self.manager.settings.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.manager.directory.stat().st_mode & 0o777, 0o700)
        self.assertTrue(self.manager.changed.is_set())

    def test_live_view_requires_a_separate_selected_camera_and_survives_updates(self):
        base = {"pairing_code": "a" * 64, "entity_ids": ["camera.private"]}
        status, _, _ = self.configure({**base, "live_camera_entity_ids": ["camera.private"]})
        self.assertEqual(status, 200)
        self.assertEqual(self.manager.options()["live_camera_entity_ids"], ["camera.private"])
        status, _, _ = self.configure(base)
        self.assertEqual(status, 200)
        self.assertEqual(self.manager.options()["live_camera_entity_ids"], ["camera.private"])
        for invalid in [["camera.other"], ["sensor.room_temperature"], [{}], "camera.private"]:
            status, _, _ = self.configure({**base, "live_camera_entity_ids": invalid})
            self.assertEqual(status, 400)

    def test_configuration_rejects_invalid_control_flags_and_external_endpoints(self):
        for extra in [{"read_only": "false"}, {"ha_url": "https://attacker.invalid"}, {"cloud_url": "https://attacker.invalid"}]:
            status, _, _ = self.configure({"pairing_code": "a" * 64, "entity_ids": ["sensor.room_temperature"], **extra})
            self.assertEqual(status, 400)
        self.assertFalse(self.manager.settings.exists())

    def test_local_control_choice_is_explicit_persistent_and_can_be_disabled(self):
        self.assertTrue(self.manager.options()["read_only"])
        self.assertEqual(self.configure({"pairing_code": "a" * 64, "entity_ids": ["binary_sensor.door"], "read_only": False})[0], 200)
        self.assertFalse(self.manager.options()["read_only"])
        self.assertFalse(self.manager.status()["read_only"])
        self.manager.credentials.write_text('{"bridge_id":"qa"}')
        self.assertEqual(self.configure({"pairing_code": "", "entity_ids": ["binary_sensor.door"]})[0], 200)
        self.assertFalse(self.manager.options()["read_only"])
        self.assertEqual(self.configure({"pairing_code": "", "entity_ids": ["binary_sensor.door"], "read_only": True})[0], 200)
        self.assertTrue(self.manager.status()["read_only"])

    def test_csrf_is_required_before_reading_home_assistant_or_writing_settings(self):
        status, _, _ = self.configure({"pairing_code": "a" * 64, "entity_ids": ["sensor.room_temperature"]}, csrf="not-the-token")
        self.assertEqual(status, 403)
        self.assertFalse(self.requests)
        self.assertFalse(self.manager.settings.exists())

    def test_invalid_unsupported_duplicate_and_unknown_entities_never_persist(self):
        for entities in [["person.parent"], ["sensor.heart"], ["sensor.missing"], [None], ["binary_sensor.door"] * 2, ["sensor.room_temperature"] * 251]:
            status, _, _ = self.configure({"pairing_code": "a" * 64, "entity_ids": entities})
            self.assertEqual(status, 400)
        self.assertFalse(self.manager.settings.exists())

    def test_initial_pairing_requires_a_real_code_and_a_selected_sensor(self):
        for code, entities in [("", ["binary_sensor.door"]), ("not-a-code", ["binary_sensor.door"]), ("a" * 64, [])]:
            self.assertEqual(self.configure({"pairing_code": code, "entity_ids": entities})[0], 400)

    def test_existing_pair_can_update_the_selection_without_reentering_any_secret(self):
        self.manager.credentials.write_text('{"bridge_id":"qa-bridge","token":"cloud-secret"}')
        self.assertEqual(self.configure({"pairing_code": "", "entity_ids": ["binary_sensor.door"]})[0], 200)
        status, _, body = self.http("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["paired"])
        self.assertNotIn(b"cloud-secret", body)
        self.assertNotIn(b"test-supervisor-secret", body)
        self.assertNotIn(b"qa-bridge", body)

    def test_clearing_the_selection_pauses_sharing_even_if_home_assistant_is_unavailable(self):
        self.manager.credentials.write_text('{"bridge_id":"qa-bridge"}')
        self.manager.request = lambda *_: (_ for _ in ()).throw(setup.BridgeError("offline"))
        self.assertEqual(self.configure({"pairing_code": "", "entity_ids": []})[0], 200)
        self.assertEqual(self.manager.options()["entity_ids"], [])
        self.assertTrue(self.manager.changed.is_set())

    def test_ended_access_requires_a_new_pairing_code(self):
        self.manager.credentials.write_text('{"bridge_id":"qa-bridge"}')
        self.manager.phase = "pairing_required"
        self.assertEqual(self.configure({"pairing_code": "", "entity_ids": ["binary_sensor.door"]})[0], 400)

    def test_oversized_and_malformed_requests_are_bounded(self):
        status, _, _ = self.http("POST", "/api/configure", "x" * 64001, {"Content-Type": "application/json", "X-HomeKind-Setup": self.manager.csrf})
        self.assertEqual(status, 413)
        status, _, _ = self.http("POST", "/api/configure", "{", {"Content-Type": "application/json", "X-HomeKind-Setup": self.manager.csrf})
        self.assertEqual(status, 400)

    def test_arbitrary_files_and_settings_are_never_served(self):
        for path in ["/api/options", "/credentials.json", "/setup.json", "/../setup_server.py", "/options.json"]:
            self.assertEqual(self.http("GET", path)[0], 404)

    def test_controller_pairs_restarts_serially_and_pauses_without_losing_credentials(self):
        runs = []
        active = [0]
        maximum = [0]
        manager = self.manager
        class FakeConnector:
            def __init__(self, options, directory, request):
                self.options = options
                self.stream_connected = True
                self.button_status = {}
                self.tracked = []
            def run(self, stop_event, on_status):
                active[0] += 1
                maximum[0] = max(maximum[0], active[0])
                runs.append(self.options)
                manager.credentials.write_text('{"bridge_id":"qa-preserved-bridge"}')
                on_status("paired")
                on_status("synced")
                stop_event.wait(3)
                self.stream_connected = False
                active[0] -= 1
        def until(predicate):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if predicate():
                    return
                time.sleep(.01)
            self.fail("Controller did not reach the expected state")
        with patch.object(setup, "Connector", FakeConnector):
            worker = threading.Thread(target=manager.run, daemon=True)
            worker.start()
            try:
                manager.configure({"pairing_code": "a" * 64, "entity_ids": ["sensor.room_temperature"]})
                until(lambda: manager.status()["phase"] == "connected")
                self.assertEqual(manager.options()["pairing_code"], "")
                manager.configure({"pairing_code": "", "entity_ids": ["binary_sensor.door"]})
                until(lambda: len(runs) == 2)
                manager.configure({"pairing_code": "", "entity_ids": []})
                until(lambda: manager.status()["phase"] == "paused")
                self.assertEqual(maximum[0], 1)
                self.assertTrue(manager.paired())
                self.assertEqual(runs[1]["entity_ids"], ["binary_sensor.door"])
            finally:
                manager.stopped.set()
                manager.changed.set()
                worker.join(3)
                self.assertFalse(worker.is_alive())

    def test_native_health_routes_require_ingress_csrf_and_preview_confirmation(self):
        import datetime as dt
        date=dt.datetime.now(dt.timezone.utc).date().isoformat()
        payload={'format':'json','content':json.dumps([{'date':date,'steps':1234,'private':'EXCLUDED_HEALTH_SECRET'}]),'timezone':'UTC','source':'garmin_export','label':'Personal Garmin'}
        headers={'Content-Type':'application/json','X-HomeKind-Setup':self.manager.csrf}
        status,_,body=self.http('POST','/api/health/preview',payload,{'Content-Type':'application/json'})
        self.assertEqual(status,403);self.assertFalse(self.manager.health.file.exists())
        self.server.ingress_ip='172.30.32.2'
        self.assertEqual(self.http('POST','/api/health/preview',payload,headers)[0],403)
        self.server.ingress_ip='127.0.0.1'
        status,response_headers,body=self.http('POST','/api/health/preview',payload,headers)
        self.assertEqual(status,200);self.assertEqual(response_headers['Cache-Control'],'no-store');preview=json.loads(body)
        self.assertNotIn(b'EXCLUDED_HEALTH_SECRET',body);self.assertFalse(self.manager.health.file.exists());self.assertFalse(self.requests)
        self.assertEqual(self.http('POST','/api/health/commit',{'preview_id':preview['preview_id']},headers)[0],400)
        published=[];self.manager.health.publish=lambda entity,value:published.append((entity,value))
        self.assertEqual(self.http('POST','/api/health/commit',{'preview_id':preview['preview_id'],'publish_to_ha':True},headers)[0],200)
        self.assertEqual(len(published),1);self.assertEqual(published[0][1]['state'],'1234')
        self.assertNotIn('EXCLUDED_HEALTH_SECRET',self.manager.health.file.read_text())
        status,_,body=self.http('GET','/api/health/status');self.assertEqual(status,200);self.assertNotIn(b'1234',body)
        for path in ['/health-imports.json','/api/health/export','/api/health/login']:
            self.assertEqual(self.http('GET',path)[0],404)


if __name__ == "__main__":
    unittest.main()
