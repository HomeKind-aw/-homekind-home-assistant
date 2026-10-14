import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("bridge", Path(__file__).parents[1]/"homekind_bridge/bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

class ConnectorTests(unittest.TestCase):
    def state(self, value="off", assumed=False, time="2026-10-14T00:00:00Z"):
        return {"entity_id":"switch.test_plug","state":value,"attributes":{"friendly_name":"Test plug","assumed_state":assumed},"last_updated":time,"last_changed":time,"context":{"id":value}}

    def run_command(self, *, denied=False, assumed=False, change=True):
        calls=[]
        def request(url, token, data=None):
            calls.append((url, data))
            if url.endswith("/authorize"):
                if denied: raise bridge.BridgeError("HTTP 403")
                return {"entity_id":"switch.test_plug","domain":"switch","service":"turn_on","desired":"on","expires_at":"2099-01-01T00:00:00Z"}
            if "/states/" in url:
                called=any("/services/" in u for u,_ in calls)
                return self.state("on" if change and called else "off", assumed, "2026-10-14T00:00:01Z" if called else "2026-10-14T00:00:00Z")
            return {}
        with tempfile.TemporaryDirectory() as directory:
            c=bridge.Connector({"ha_url":"http://local-test","read_only":False,"entity_ids":["switch.test_plug","sensor.room_temperature"]},directory,request=request,sleeper=lambda _:None)
            c.ha_token="local-only-token";c.credentials["bridge_id"]="fixture";c.tracked=[{"entity_id":"switch.test_plug","control_enabled":True}]
            c.execute({"id":"fixture-command","lease_id":"fixture-lease","entity_id":"switch.test_plug"})
        return calls

    def test_authorization_denial_never_calls_home_assistant_service(self):
        calls=self.run_command(denied=True)
        self.assertFalse(any("/services/" in u for u,_ in calls))

    def test_real_service_path_and_observed_change_are_required(self):
        calls=self.run_command();service=[(u,d) for u,d in calls if "/services/" in u]
        self.assertEqual(len(service),1);self.assertTrue(service[0][0].endswith("/api/services/switch/turn_on"));self.assertEqual(service[0][1],{"entity_id":"switch.test_plug"})
        self.assertEqual(calls[-1][1]["status"],"confirmed")
        self.assertFalse(any("/api/states/" in u and d is not None for u,d in calls))

    def test_optimistic_state_is_reported_without_physical_confirmation(self):
        self.assertEqual(self.run_command(assumed=True)[-1][1]["status"],"reported")

    def test_unobserved_state_change_is_unconfirmed(self):
        self.assertEqual(self.run_command(change=False)[-1][1]["status"],"unconfirmed")

    def test_catalog_excludes_cameras_people_and_personal_health(self):
        fixtures=[{"entity_id":"camera.room","attributes":{}},{"entity_id":"person.parent","attributes":{}},{"entity_id":"sensor.heart","attributes":{"device_class":"heart_rate"}},{"entity_id":"sensor.generic_text","attributes":{}}]
        self.assertFalse(any(bridge.safe_entity(x) for x in fixtures))
        self.assertTrue(bridge.safe_entity({"entity_id":"sensor.temperature","attributes":{"device_class":"temperature"}}))

    def test_numeric_sensor_does_not_send_arbitrary_text(self):
        self.assertFalse(bridge.safe_reading({"entity_id":"sensor.temperature","state":"private text"}))
        self.assertTrue(bridge.safe_reading({"entity_id":"sensor.temperature","state":"21.4"}))

    def test_lost_service_response_is_unconfirmed_and_is_not_retried(self):
        calls=[]
        def request(url, token, data=None):
            calls.append((url,data))
            if url.endswith('/authorize'):
                return {"entity_id":"switch.test_plug","domain":"switch","service":"turn_on","desired":"on","expires_at":"2099-01-01T00:00:00Z"}
            if '/states/' in url: return self.state()
            if '/services/' in url: raise bridge.BridgeError('Connection unavailable')
            return {}
        with tempfile.TemporaryDirectory() as directory:
            c=bridge.Connector({"ha_url":"http://local-test","read_only":False,"entity_ids":["switch.test_plug","sensor.room_temperature"]},directory,request=request);c.ha_token='local';c.credentials['bridge_id']='fixture';c.tracked=[{"entity_id":"switch.test_plug","control_enabled":True}]
            c.execute({"id":"fixture-command","lease_id":"fixture-lease","entity_id":"switch.test_plug"})
        self.assertEqual(sum('/services/' in u for u,_ in calls),1)
        self.assertEqual(calls[-1][1]['status'],'unconfirmed')

    def test_cloud_receives_only_selected_values_and_small_catalog(self):
        calls=[]
        def request(url,token,data=None):
            calls.append((url,token,data))
            if url.endswith("/api/states"):
                return [self.state(),{"entity_id":"sensor.room_temperature","state":"21.0","attributes":{"device_class":"temperature","unit_of_measurement":"°C"},"last_updated":"2026-10-14T00:00:00Z","last_changed":"2026-10-14T00:00:00Z"}]
            return {"tracked":[]}
        with tempfile.TemporaryDirectory() as directory:
            c=bridge.Connector({"ha_url":"http://local-test","read_only":False,"entity_ids":["switch.test_plug","sensor.room_temperature"]},directory,request=request);c.ha_token="local-ha-secret";c.credentials["bridge_id"]="fixture";c.tracked=[{"entity_id":"switch.test_plug"}];c.sync()
        cloud=calls[-1];self.assertNotEqual(cloud[1],"local-ha-secret");self.assertEqual(len(cloud[2]["states"]),1);self.assertNotIn("state",cloud[2]["catalog"][0]);self.assertEqual(len(cloud[2]["catalog"]),2)

if __name__ == "__main__": unittest.main()
