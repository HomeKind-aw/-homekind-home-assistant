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



class NativeButtonTests(unittest.TestCase):
    def discovery(self, actions=('single','double','triple','hold','release')):
        triggers=[{'platform':'device','domain':'mqtt','device_id':'selected-device','type':'action','subtype':a} for a in actions]
        info={'triggers':[{'discovery_data':{'payload':{'automation_type':'trigger','type':'action','subtype':a,'topic':'zigbee2mqtt/Magic Button/action','payload':a}}} for a in actions]}
        return info,triggers

    def test_discovery_matches_selected_device_and_rejects_wildcards_templates_and_unknown_actions(self):
        info,triggers=self.discovery()
        rows=bridge.mqtt_button_bindings(info,triggers,'selected-device')
        self.assertEqual({r['action'] for r in rows},{'single','double','triple','hold','release'})
        self.assertEqual(bridge.mqtt_button_bindings(info,triggers,'another-device'),[])
        for change in [{'topic':'zigbee2mqtt/#'},{'value_template':'{{ secret }}'},{'payload':'turn_off'},{'type':[]},{'subtype':[]}]:
            bad=json.loads(json.dumps(info));bad['triggers'][0]['discovery_data']['payload'].update(change)
            self.assertEqual(len(bridge.mqtt_button_bindings(bad,triggers,'selected-device')),4)
        info,triggers=self.discovery(('quadruple','turn_off'))
        self.assertEqual(bridge.mqtt_button_bindings(info,triggers,'selected-device'),[])

    def test_json_action_template_is_parsed_without_evaluating_arbitrary_templates(self):
        info,triggers=self.discovery(('triple',))
        info['triggers'][0]['discovery_data']['payload']['value_template']='{{ value_json.action }}'
        self.assertTrue(bridge.mqtt_button_bindings(info,triggers,'selected-device')[0]['json_action'])

    def test_native_event_entity_standard_names_include_triple(self):
        for raw,action in [('single_press','single'),('double_press','double'),('triple_press','triple'),('long_press_start','hold'),('long_press_end','release')]:
            s=state('event.aqara', '2026-10-17T00:00:00Z', klass='button');s['attributes']['event_type']=raw
            self.assertEqual(bridge.observation(s)['event_type'],action)

    def test_real_ha_socket_discovers_existing_actions_rejects_retained_and_preserves_press_bursts(self):
        from websockets.sync.server import serve
        info,triggers=self.discovery();calls=[];done=threading.Event();errors=[]
        stamp=bridge.dt.datetime.now(bridge.dt.timezone.utc).isoformat()
        battery=state('sensor.magic_button_battery','100',stamp,'battery');battery['attributes']['friendly_name']='Magic Button Battery'
        virtual=bridge.button_candidates([battery],stamp)[0]['entity_id']
        def handler(ws):
            try:
                ws.send(json.dumps({'type':'auth_required'}));ws.recv();ws.send(json.dumps({'type':'auth_ok'}))
                while not done.is_set():
                    query=json.loads(ws.recv(timeout=5));calls.append(query)
                    kind=query['type'];result=None
                    if kind=='config/entity_registry/list':result=[{'entity_id':battery['entity_id'],'device_id':'selected-device','platform':'mqtt','entity_category':'diagnostic'}]
                    elif kind=='config/device_registry/list':result=[{'id':'selected-device'}]
                    elif kind=='config/area_registry/list':result=[]
                    elif kind=='device_automation/trigger/list':result=triggers
                    elif kind=='mqtt/device/debug_info':result=info
                    elif kind not in ('subscribe_events','mqtt/subscribe'):raise AssertionError(kind)
                    ws.send(json.dumps({'id':query['id'],'type':'result','success':True,'result':result}))
                    if kind=='mqtt/subscribe':
                        for payload,retain,topic in [('single',True,query['topic']),('turn_off',False,query['topic']),('single',False,'unselected/action'),('single',False,query['topic']),('double',False,query['topic']),('triple',False,query['topic']),('hold',False,query['topic'])]:
                            ws.send(json.dumps({'id':query['id'],'type':'event','event':{'topic':topic,'payload':payload,'retain':retain,'qos':0}}))
                        ws.send(json.dumps({'id':5,'type':'event','event':{'event_type':'homekind_button','time_fired':stamp,'data':{'source_entity_id':battery['entity_id'],'action':'single'}}}))
                        done.wait(5);return
            except Exception as e:errors.append(e)
        with serve(handler,'127.0.0.1',0) as server,tempfile.TemporaryDirectory() as directory:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            c=bridge.Connector({'ha_url':f'http://127.0.0.1:{server.socket.getsockname()[1]}','entity_ids':[virtual]},directory,request=lambda *args:[battery]);c.ha_token='local-fixture';c.tracked=[{'entity_id':virtual}]
            stream=bridge.HAStream(c);stream.start()
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and c.current.get(virtual,{}).get('attributes',{}).get('event_type')!='hold':time.sleep(.01)
            done.set();stream.stop();server.shutdown();thread.join(timeout=2)
            self.assertFalse(errors,errors)
            presses=[v for v in c.pending.values() if v.get('event_type')]
            self.assertEqual([v['event_type'] for v in presses],['single','double','triple','hold'])
            self.assertEqual(len({v['event_id'] for v in presses}),4)
            self.assertNotIn('ha_entity_category',c.registry[virtual])
            self.assertEqual(c.button_status[virtual]['actions'],['double','hold','release','single','triple'])
            self.assertNotIn('sensor.magic_button_battery',c.current)
            mqtt=[v for v in calls if v['type']=='mqtt/subscribe'];self.assertEqual(len(mqtt),1);self.assertEqual(mqtt[0]['topic'],'zigbee2mqtt/Magic Button/action')
            self.assertNotIn('fire_event',str(calls));self.assertNotIn('call_service',str(calls));self.assertNotIn('automation/config',str(calls))
            restored=bridge.Connector({'entity_ids':[virtual]},directory)
            self.assertEqual([v['event_type'] for v in restored.pending.values() if v.get('event_type')],['single','double','triple','hold'])
            sent=[];c.credentials['bridge_id']='synthetic-button-bridge';c.request=lambda url,token,payload:(sent.append(payload) or {'tracked':[{'entity_id':virtual}]});c.stream_connected=True;c.stream_sync()
            self.assertEqual([v['event_type'] for v in sent[-1]['states'] if v.get('event_type')],['single','double','triple','hold']);self.assertEqual(c.pending,{})
            self.assertNotIn('zigbee2mqtt',str(sent));self.assertNotIn('payload',str(sent))

    def test_subscription_denied_keeps_other_sensor_stream_and_reports_button_unconnected(self):
        with tempfile.TemporaryDirectory() as directory:
            c=bridge.Connector({'entity_ids':[]},directory);s=bridge.HAStream(c,connect=lambda:None)
            c.stream_connected=True;c.button_sources={'sensor.button_battery':'event.virtual'};c.current['event.virtual']={'attributes':{'friendly_name':'Button press'}}
            s.button_requests={};s.button_subscriptions={('sensor.button_battery','selected/action',False):10};s.button_routes={10:{'source':'sensor.button_battery','topic':'selected/action','json_action':False,'actions':{'single':'single'},'active':False}}
            s.button_result(None,{'id':10,'success':False,'error':{'code':'unauthorized'}})
            self.assertTrue(c.stream_connected);self.assertEqual(c.native_button_sources,set());self.assertIn('permission',c.button_status['event.virtual']['message']);self.assertEqual(s.button_subscriptions,{})

if __name__=='__main__':unittest.main()
