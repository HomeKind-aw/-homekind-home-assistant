import json
import importlib.util
from pathlib import Path
import tempfile
import unittest
import threading
import time
from unittest.mock import patch

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

    def test_catalog_excludes_people_and_unclassified_personal_text(self):
        fixtures=[{"entity_id":"person.parent","attributes":{}},{"entity_id":"sensor.heart","attributes":{"device_class":"heart_rate"}},{"entity_id":"sensor.generic_text","attributes":{}}]
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

    def test_uploads_continue_while_a_device_command_is_slow(self):
        class NoStream:
            def __init__(self, connector): pass
            def start(self): pass
            def stop(self): pass
        entered, release, uploaded, stop = [threading.Event() for _ in range(4)]
        claims=[{"id":"one"}]
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge, 'HAStream', NoStream):
            c=bridge.Connector({"read_only":False}, directory)
            c.enrol=lambda:None;c.ready.set()
            def cloud(operation, data=None):
                return {"commands":[claims.pop()] if claims else []}
            c.cloud=cloud
            def execute(claim): entered.set();release.wait(4)
            c.execute=execute
            def sync():
                if entered.is_set(): uploaded.set()
            c.stream_sync=sync
            thread=threading.Thread(target=c.run,args=(stop,));thread.start()
            try:
                self.assertTrue(entered.wait(2))
                c.next_sync=0;c.wake.set()
                self.assertTrue(uploaded.wait(2), 'A slow command blocked sensor uploads')
            finally:
                stop.set();release.set();c.wake.set();thread.join(4)
            self.assertFalse(thread.is_alive())

    def test_one_slow_device_does_not_block_other_leased_commands(self):
        entered=threading.Barrier(4);release=threading.Event();stop=threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            c=bridge.Connector({"read_only":False},directory);c.external_stop=stop;c.ready.set()
            claims=[{"id":str(i)} for i in range(3)]
            c.cloud=lambda *args:{"commands":claims}
            def execute(claim):entered.wait(timeout=3);release.wait(3)
            c.execute=execute
            thread=threading.Thread(target=c.command_loop,args=(lambda _:None,));thread.start()
            try:entered.wait(timeout=3)
            finally:stop.set();release.set();thread.join(4)
            self.assertFalse(thread.is_alive())

    def test_stopping_during_authorization_prevents_a_physical_command(self):
        calls=[];stop=threading.Event()
        def request(url, token, data=None):
            calls.append(url)
            if '/states/' in url:return self.state()
            if url.endswith('/authorize'):
                stop.set()
                return {"entity_id":"switch.test_plug","domain":"switch","service":"turn_on","desired":"on","expires_at":"2099-01-01T00:00:00Z"}
            return {}
        with tempfile.TemporaryDirectory() as directory:
            c=bridge.Connector({"read_only":False,"entity_ids":["switch.test_plug"]},directory,request=request)
            c.ha_token='local';c.credentials['bridge_id']='fixture';c.external_stop=stop;c.tracked=[{"entity_id":"switch.test_plug","control_enabled":True}]
            c.execute({"id":"one","lease_id":"lease","entity_id":"switch.test_plug"})
        self.assertFalse(any('/services/' in url for url in calls))

    def test_heartbeat_persists_one_batch_instead_of_each_device(self):
        with tempfile.TemporaryDirectory() as directory:
            entities=['switch.test_'+str(i) for i in range(80)]
            c=bridge.Connector({"entity_ids":entities},directory,request=lambda *args:{"tracked":[]})
            c.credentials['bridge_id']='fixture';c.tracked=[{"entity_id":e} for e in entities];c.stream_connected=True
            c.current={e:{**self.state(),"entity_id":e} for e in entities}
            with patch.object(c,'save_pending') as save:c.stream_sync()
            self.assertEqual(save.call_count,2)

    def test_command_revocation_stops_the_connector_without_changing_the_settings_event(self):
        class NoStream:
            def __init__(self, connector):pass
            def start(self):pass
            def stop(self):pass
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge,'HAStream',NoStream):
            c=bridge.Connector({"read_only":False},directory);c.enrol=lambda:None;c.stream_sync=lambda:c.ready.set()
            def revoked(*args):raise bridge.AccessEnded('fixture')
            c.cloud=revoked;settings=threading.Event()
            with self.assertRaises(bridge.AccessEnded):c.run(settings)
            self.assertFalse(settings.is_set())

    def test_media_players_are_supported_without_sending_titles_or_artwork(self):
        fixture={**self.state(),"entity_id":"media_player.living_room_tv","state":"playing","attributes":{"device_class":"tv","friendly_name":"Living room TV","media_title":"Private programme","media_artist":"Private artist","entity_picture":"https://private.invalid/art"}}
        self.assertTrue(bridge.safe_entity(fixture));self.assertTrue(bridge.safe_reading(fixture))
        reading=bridge.observation(fixture);self.assertEqual(reading['state'],'playing');self.assertEqual(reading['device_class'],'tv');self.assertNotIn('Private',str(reading));self.assertNotIn('entity_picture',str(reading))
        for klass in ['speaker','receiver','projector','']:
            fixture['attributes']['device_class']=klass;self.assertTrue(bridge.safe_entity(fixture))

    def test_media_players_only_send_known_playback_states(self):
        for value in ['playing','paused','idle','buffering','on','off','standby','unknown','unavailable']:
            self.assertTrue(bridge.safe_reading({'entity_id':'media_player.speaker','state':value}))
        self.assertFalse(bridge.safe_reading({'entity_id':'media_player.speaker','state':'Private programme title'}))
        self.assertTrue(bridge.ENTITY.fullmatch('media_player.living_room_tv'))

    def test_media_players_cannot_use_light_or_plug_service_commands(self):
        calls=[]
        with tempfile.TemporaryDirectory() as directory:
            c=bridge.Connector({'read_only':False,'entity_ids':['media_player.tv']},directory,request=lambda *args:calls.append(args));c.ha_token='local'
            c.execute({'id':'synthetic','lease_id':'synthetic','entity_id':'media_player.tv'})
        self.assertEqual(calls,[])

if __name__ == "__main__": unittest.main()


class ExtendedReadingTests(unittest.TestCase):
    def source(self, entity, value="72", unit="bpm", klass="", name="Heart rate"):
        return {"entity_id":entity,"state":value,"attributes":{"device_class":klass,"friendly_name":name,"unit_of_measurement":unit},"last_updated":"2026-10-16T00:00:00Z","last_changed":"2026-10-16T00:00:00Z"}

    def test_camera_states_never_send_image_tokens_or_stream_urls(self):
        s=self.source("camera.front", "recording", "", "", "Front camera")
        s["attributes"].update(access_token="private-token",entity_picture="/api/private",stream_source="rtsp://private",brand="private")
        self.assertTrue(bridge.safe_entity(s));self.assertTrue(bridge.safe_reading(s))
        wire=bridge.observation(s);catalog=bridge.catalog_entry(s)
        self.assertEqual(wire["state"],"recording")
        self.assertFalse(any(k in str(wire)+str(catalog) for k in ["private-token","rtsp://","/api/private"]))
        for value in ["idle","streaming","recording","on","off","unknown","unavailable"]:
            self.assertTrue(bridge.safe_reading({**s,"state":value}))
        self.assertFalse(bridge.safe_reading({**s,"state":"private image"}))

    def test_doorbell_event_keeps_timestamp_and_only_ring_type(self):
        s=self.source("event.front_doorbell", "2026-10-16T00:00:00Z", "", "doorbell", "Front doorbell")
        s["attributes"].update(event_type="ring",video_url="https://private")
        self.assertTrue(bridge.safe_entity(s));self.assertTrue(bridge.safe_reading(s))
        self.assertEqual(bridge.observation(s)["event_type"],"ring")
        self.assertNotIn("video_url",bridge.observation(s))
        s["attributes"]["event_type"]="motion"
        self.assertEqual(bridge.observation(s)["event_type"],"")
        self.assertTrue(bridge.safe_entity(self.source("event.remote", "2026-10-16T00:00:00Z", "", "button", "Remote")))
        self.assertTrue(bridge.safe_entity(self.source("binary_sensor.front_doorbell", "on", "", "", "Front doorbell")))
        self.assertFalse(bridge.safe_reading({**s,"state":"not-a-time"}))

    def test_health_classification_requires_matching_metric_and_units(self):
        fixtures=[("Steps","steps","","health_steps"),("Sleep duration","h","duration","health_sleep"),("Resting heart rate","bpm","","health_resting_hr"),("Heart rate","bpm","","health_heart_rate"),("SpO2","%","","health_oxygen"),("Body weight","kg","weight","health_weight"),("Body temperature","°C","temperature","health_body_temperature"),("Systolic pressure","mmHg","","health_systolic"),("Diastolic pressure","mmHg","","health_diastolic"),("Glucose","mmol/L","blood_glucose_concentration","health_glucose")]
        for name,unit,klass,expected in fixtures:
            s=self.source("sensor.personal_metric",unit=unit,klass=klass,name=name)
            self.assertTrue(bridge.safe_entity(s),name);self.assertEqual(bridge.observation(s)["device_class"],expected)
        self.assertEqual(bridge.reading_class(self.source("sensor.lounge","21","°C","temperature","Lounge temperature")),"temperature")
        self.assertFalse(bridge.safe_entity(self.source("sensor.heart_rate","private text","","","Heart rate")))
        self.assertFalse(bridge.safe_entity(self.source("sensor.personal_notes","private text","","","Personal notes")))

    def test_health_outbox_requires_current_personal_collection_flag(self):
        sent=[]
        def request(url,token,data=None):
            sent.append(data);return {"tracked":[{"entity_id":"sensor.heart_rate","kind":"health","collect_health":False}]}
        with tempfile.TemporaryDirectory() as directory:
            c=bridge.Connector({"entity_ids":["sensor.heart_rate"]},directory,request=request,clock=lambda:1792108800)
            c.credentials["bridge_id"]="fixture";c.stream_connected=True
            s=self.source("sensor.heart_rate")
            c.tracked=[{"entity_id":"sensor.heart_rate","kind":"health","collect_health":False}]
            c.accept_state(s);self.assertFalse(c.pending);c.stream_sync();self.assertEqual(sent[-1]["states"],[])
            self.assertEqual(sent[-1]["catalog"][0]["device_class"],"health_heart_rate")
            c.tracked=[{"entity_id":"sensor.heart_rate","kind":"health","collect_health":True}]
            c.accept_state(s);self.assertIn("sensor.heart_rate",c.pending)
            # Pausing collection drops both durable pending values and cache heartbeats.
            c.tracked=[{"entity_id":"sensor.heart_rate","kind":"health","collect_health":False}]
            c.stream_sync();self.assertEqual(sent[-1]["states"],[]);self.assertFalse(c.pending)

    def test_extended_sources_can_never_be_sent_as_on_off_controls(self):
        calls=[]
        with tempfile.TemporaryDirectory() as directory:
            c=bridge.Connector({"entity_ids":["camera.front","event.doorbell","sensor.heart_rate"],"read_only":False},directory,request=lambda *args:calls.append(args))
            for entity in c.allowlist:c.execute({"entity_id":entity})
        self.assertEqual(calls,[])


if __name__ == "__main__":
    unittest.main()

class CommissioningReadingTests(unittest.TestCase):
    def test_vacuum_status_strips_attributes_and_rejects_actions(self):
        state = {"entity_id": "vacuum.hoover", "state": "docked", "attributes": {"friendly_name": "Hoover", "access_token": "private", "entity_picture": "private-url"}}
        self.assertTrue(bridge.safe_entity(state))
        self.assertTrue(bridge.safe_reading(state))
        self.assertEqual(bridge.observation(state)["device_class"], "vacuum")
        self.assertNotIn("private", str(bridge.observation(state)))
        self.assertFalse(bridge.safe_reading({**state, "state": "unlock"}))

    def test_selected_button_uses_fixed_fresh_actions_and_not_battery_updates(self):
        now = time.time()
        stamp = bridge.dt.datetime.fromtimestamp(now, bridge.dt.timezone.utc).isoformat()
        source = {"entity_id": "sensor.magic_button_battery", "state": "100", "last_updated": stamp, "last_changed": stamp, "attributes": {"friendly_name": "Magic Button Battery", "device_class": "battery"}}
        candidate = bridge.button_candidates([source], stamp)[0]
        with tempfile.TemporaryDirectory() as directory:
            connector = bridge.Connector({"entity_ids": [candidate["entity_id"]]}, directory, clock=lambda: now + 1)
            connector.tracked = [{"entity_id": candidate["entity_id"], "id": "button", "kind": "other"}]
            connector.current[candidate["entity_id"]] = candidate
            connector.button_sources = {source["entity_id"]: candidate["entity_id"]}
            at = bridge.dt.datetime.fromtimestamp(now + 0.5, bridge.dt.timezone.utc).isoformat()
            event = {"event_type": "homekind_button", "time_fired": at, "data": {"source_entity_id": source["entity_id"], "action": "single"}}
            connector.accept_button_event({**event, "data": {**event["data"], "action": "turn_off"}})
            self.assertEqual(connector.pending, {})
            connector.accept_button_event({**event, "time_fired": "2020-01-01T00:00:00Z"})
            self.assertEqual(connector.pending, {})
            connector.accept_button_event(event)
            self.assertEqual(next(iter(connector.pending.values()))["event_type"], "single")
            self.assertEqual(next(iter(connector.pending.values()))["state"], at)

    def test_normal_buttons_do_not_become_doorbells(self):
        state = {"entity_id": "event.remote", "state": "2026-10-17T00:00:00Z", "attributes": {"device_class": "button", "event_type": "double"}}
        self.assertEqual(bridge.observation(state)["device_class"], "button")
        self.assertEqual(bridge.observation(state)["event_type"], "double")

class WeatherReadingTests(unittest.TestCase):
    def source(self, **attrs):
        return {"entity_id":"weather.home", "state":"partlycloudy", "last_updated":"2026-10-16T00:00:00Z", "last_changed":"2026-10-16T00:00:00Z", "attributes":{"friendly_name":"Home weather", "temperature":18.4, "temperature_unit":"°C", "humidity":63, "latitude":-37.8, "longitude":144.8, "access_token":"private", "forecast":[{"private":"forecast"}], **attrs}}

    def test_weather_discovery_and_observation_strip_coordinates_tokens_and_forecasts(self):
        state=self.source();self.assertTrue(bridge.safe_entity(state));self.assertTrue(bridge.safe_reading(state));value=bridge.observation(state)
        self.assertEqual(value['temperature'],18.4);self.assertEqual(value['humidity'],63);self.assertEqual(value['temperature_unit'],'°C')
        for field in ['latitude','longitude','access_token','forecast']:self.assertNotIn(field,value)
        state['state']='some private arbitrary text';self.assertFalse(bridge.safe_reading(state))
        self.assertNotIn('temperature',bridge.observation(self.source(temperature=float('nan'))))
        self.assertNotIn('temperature',bridge.observation(self.source(temperature=True)))

    def test_only_locally_selected_weather_is_shared_without_device_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            c=bridge.Connector({'entity_ids':['weather.home']},directory);c.credentials['bridge_id']='fixture';c.stream_connected=True
            c.current={'weather.home':self.source(),'weather.other':{**self.source(),'entity_id':'weather.other'}}
            payloads=[];c.cloud=lambda operation,payload:payloads.append(payload) or {'tracked':[]}
            c.stream_sync();self.assertEqual(len(payloads[0]['bridge_info']['weather']),1);self.assertEqual(payloads[0]['states'],[])
            self.assertNotIn('private',json.dumps(payloads[0]['bridge_info']['weather']))
            c.allowlist.clear();c.stream_sync();self.assertEqual(payloads[1]['bridge_info']['weather'],[])
            c.allowlist={'weather.home'};c.stream_connected=False;c.stream_sync();self.assertEqual(payloads[2]['bridge_info']['weather'],[])
