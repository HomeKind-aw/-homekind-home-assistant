import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

spec = importlib.util.spec_from_file_location('homekind_bridge_stream', Path(__file__).parents[1]/'homekind_bridge/bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
NOW = 1791972000

def state(entity='binary_sensor.lounge', value='on', at='2026-10-14T00:00:01Z', klass='occupancy'):
    return {'entity_id':entity,'state':value,'attributes':{'device_class':klass,'friendly_name':'Pilot fixture'},'last_updated':at,'last_changed':at}

class StreamTests(unittest.TestCase):
    def connector(self, directory, request=lambda *args: {'tracked': []}, **options):
        c=bridge.Connector({'ha_url':'http://localhost:8123','entity_ids':['binary_sensor.lounge'], **options},directory,request=request,clock=lambda:NOW)
        c.ha_token='local-fixture-only';c.credentials['bridge_id']='fixture';c.tracked=[{'entity_id':'binary_sensor.lounge'}]
        return c

    def test_read_only_is_default_and_never_dispatches_a_service(self):
        calls=[]
        with tempfile.TemporaryDirectory() as d:
            c=self.connector(d,request=lambda *args:calls.append(args))
            self.assertTrue(c.read_only)
            with self.assertRaises(bridge.BridgeError):c.execute({'entity_id':'light.example'})
        self.assertEqual(calls,[])

    def test_local_allowlist_is_required_and_registry_does_not_expand_it(self):
        with tempfile.TemporaryDirectory() as d:
            c=self.connector(d,entity_ids=[])
            c.accept_state(state());self.assertEqual(c.current,{})
            s=bridge.HAStream(c,connect=lambda *args:None)
            s.registry({2:[{'entity_id':'binary_sensor.lounge','unique_id':'private-device'}]})
            self.assertEqual(c.registry,{})

    def test_latest_state_outbox_is_durable_bounded_and_private(self):
        with tempfile.TemporaryDirectory() as d:
            c=self.connector(d);c.accept_state(state());first=c.pending['binary_sensor.lounge']['event_id']
            c.accept_state(state(),observed_at='2026-10-14T01:00:00Z')
            self.assertEqual(c.pending['binary_sensor.lounge']['event_id'],first)
            c.accept_state(state(value='off',at='2026-10-14T00:00:02Z'))
            self.assertEqual(len(c.pending),1);self.assertNotEqual(c.pending['binary_sensor.lounge']['event_id'],first)
            self.assertEqual(os.stat(c.outbox_file).st_mode&0o777,0o600)
            self.assertEqual(os.stat(c.directory).st_mode&0o777,0o700)
            restored=self.connector(d);self.assertEqual(restored.pending['binary_sensor.lounge']['state'],'off')
            c.accept_state(state(entity='camera.room'));self.assertEqual(len(c.pending),1)

    def test_old_snapshot_never_overwrites_a_newer_event(self):
        with tempfile.TemporaryDirectory() as d:
            c=self.connector(d);c.accept_state(state(value='off',at='2026-10-14T00:00:02Z'));c.accept_state(state())
            self.assertEqual(c.current['binary_sensor.lounge']['state'],'off')
            c.accept_state(state(at='2026-10-14T00:00:02'))
            self.assertEqual(c.current['binary_sensor.lounge']['last_updated'],'2026-10-14T00:00:02Z')

    def test_outage_does_not_mark_buffered_data_current(self):
        calls=[]
        def request(url,token,payload):calls.append(payload);return {'tracked':[{'entity_id':'binary_sensor.lounge'}]}
        with tempfile.TemporaryDirectory() as d:
            c=self.connector(d,request=request);c.accept_state(state());event=c.pending['binary_sensor.lounge']['event_id'];c.stream_sync()
            self.assertFalse(calls[-1]['ha_connected']);self.assertEqual(calls[-1]['states'],[])
            self.assertEqual(c.pending['binary_sensor.lounge']['event_id'],event)
            c.stream_connected=True;c.stream_sync();self.assertEqual(calls[-1]['states'][0]['event_id'],event);self.assertEqual(c.pending,{})
            self.assertNotEqual(calls[-1].get('token'),'local-fixture-only')

    def test_cloud_error_retains_original_observation_and_revocation_stops(self):
        def failed(*args):raise bridge.AccessEnded('Access ended')
        with tempfile.TemporaryDirectory() as d:
            c=self.connector(d,request=failed);c.stream_connected=True;c.accept_state(state());before=dict(c.pending)
            with self.assertRaises(bridge.AccessEnded):c.stream_sync()
            self.assertEqual(c.pending,before)

    def test_mapping_revocation_discards_pending_readings_before_delivery(self):
        calls=[]
        with tempfile.TemporaryDirectory() as d:
            c=self.connector(d,request=lambda u,t,p:(calls.append(p) or {'tracked':[]}));c.accept_state(state());c.tracked=[];c.stream_connected=True;c.stream_sync()
            self.assertEqual(calls[-1]['states'],[])

    def test_registry_returns_only_safe_identity_metadata_for_allowlisted_entities(self):
        with tempfile.TemporaryDirectory() as d:
            c=self.connector(d);s=bridge.HAStream(c,connect=lambda *args:None)
            s.registry({2:[{'entity_id':'binary_sensor.lounge','unique_id':'abc','platform':'mqtt','device_id':'dev','secret':'excluded'},{'entity_id':'camera.private','unique_id':'not-shared'}],3:[{'id':'dev','area_id':'lounge'}],4:[{'area_id':'lounge','name':'Living room 1'}]})
            self.assertEqual(c.registry['binary_sensor.lounge']['ha_area_name'],'Living room 1')
            self.assertNotIn('camera.private',c.registry);self.assertNotIn('secret',str(c.registry))

    def test_supervisor_uses_its_dedicated_websocket_proxy(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'SUPERVISOR_TOKEN':'supervisor-local-fixture'}):
            c=self.connector(d)
            self.assertEqual(c.ha_url,'http://supervisor/core/api')
            calls=[]
            def connect(url,**kwargs):
                calls.append((url,kwargs));stream.stopped.set();raise OSError('synthetic stop')
            stream=bridge.HAStream(c,connect=connect);stream.run()
            self.assertEqual(calls[0][0],'ws://supervisor/core/websocket')
            self.assertIsNone(calls[0][1]['proxy'])
            self.assertFalse(c.stream_connected)

    def test_restart_after_clearing_pairing_code_reuses_private_credentials(self):
        calls=[]
        with tempfile.TemporaryDirectory() as d:
            c=self.connector(d,request=lambda *args:calls.append(args))
            c.credentials['pairing_used']='old-fixture-pairing';c.save();c.enrol()
            self.assertEqual(calls,[])
            self.assertEqual(os.stat(c.file).st_mode&0o777,0o600)

    def test_real_local_websocket_auth_subscription_snapshot_and_event(self):
        from websockets.sync.server import serve
        calls=[];done=threading.Event()
        def handler(ws):
            ws.send(json.dumps({'type':'auth_required'}));auth=json.loads(ws.recv());self.assertEqual(auth['type'],'auth');calls.append('auth')
            ws.send(json.dumps({'type':'auth_ok'}));sub=json.loads(ws.recv());self.assertEqual(sub,{'id':1,'type':'subscribe_events','event_type':'state_changed'});calls.append('subscribe')
            ws.send(json.dumps({'id':1,'type':'result','success':True,'result':None}))
            for _ in range(3):
                query=json.loads(ws.recv());self.assertNotIn('call_service',query['type']);ws.send(json.dumps({'id':query['id'],'type':'result','success':False}))
            ws.send(json.dumps({'id':1,'type':'event','event':{'data':{'entity_id':'binary_sensor.lounge','new_state':state(value='off',at='2026-10-14T00:00:02Z')}}}))
            done.wait(3)
        with serve(handler,'127.0.0.1',0) as server, tempfile.TemporaryDirectory() as d:
            server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
            def request(url,token,data=None):calls.append('snapshot');self.assertEqual(calls[:2],['auth','subscribe']);return [state()]
            c=self.connector(d,request=request);c.ha_url=f'http://127.0.0.1:{server.socket.getsockname()[1]}/api'
            stream=bridge.HAStream(c);stream.start()
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                if c.current.get('binary_sensor.lounge',{}).get('state')=='off':break
                time.sleep(.01)
            self.assertTrue(c.stream_connected);self.assertEqual(c.current['binary_sensor.lounge']['state'],'off');self.assertEqual(c.pending['binary_sensor.lounge']['state'],'off')
            done.set();stream.stop();server.shutdown();server_thread.join(timeout=2)

if __name__=='__main__':unittest.main()
