import sys
import json
import threading
import unittest
from pathlib import Path
root = Path(__file__).parents[1]
sys.path.insert(0, str(root / 'homekind_bridge' if (root / 'homekind_bridge').exists() else root / 'bridge' / 'homekind_bridge'))
import device_diagnostics as d


class DeviceDiagnosticTests(unittest.TestCase):
    def options(self):
        return {'managed_settings': True, 'management_id': 'a'*32, 'entity_ids': ['binary_sensor.bed']}

    def payload(self):
        return {'protocol': 1, 'management_id': 'a'*32, 'capture': {'id': '12345678-1234-4234-8234-123456789012', 'entity_id': 'binary_sensor.bed', 'ha_device_id': 'b'*32, 'ha_unique_id': 'bed_presence_zigbee2mqtt', 'expires_at': '2026-11-18T00:00:30Z', 'last_sequence': 0}}

    def test_request_is_bound_to_selected_device_short_lease_and_local_management(self):
        now=d.expiry('2026-11-18T00:00:00Z')
        self.assertIsNotNone(d.checked(self.payload(), self.options(), now))
        for field, value in [('protocol', 2), ('management_id', 'c'*32)]:
            self.assertIsNone(d.checked({**self.payload(), field:value}, self.options(), now))
        for change in [{'entity_id':'binary_sensor.other'}, {'expires_at':'2026-11-18T01:00:00Z'}, {'expires_at':'2026-11-17T00:00:00Z'}, {'url':'http://other'}, {'last_sequence':True}, {'ha_device_id':'other'}]:
            p=self.payload();p['capture'].update(change);self.assertIsNone(d.checked(p,self.options(),now))
        self.assertIsNone(d.checked(self.payload(), {**self.options(),'managed_settings':False},now))

    def test_only_fixed_scalar_fields_leave_the_home(self):
        self.assertEqual(d.values({'presence':True,'motion_sensitivity':'high','password':'secret','name':'person','camera':{},'battery':float('nan'),'linkquality':True}),{'presence':True,'motion_sensitivity':'high'})
        self.assertEqual(d.values({'presence':'true','motion_sensitivity':'arbitrary'}),{})

    def test_state_projection_discards_attributes_context_and_text(self):
        self.assertEqual(d.state_values({'state':'on','attributes':{'token':'secret'},'context':{'user_id':'secret'}}),{'state':'on'})
        self.assertEqual(d.state_values({'state':'private text'}),{})

    def test_mqtt_topic_comes_from_exact_discovery_without_wildcards_or_commands(self):
        info=lambda topic:{'entities':[{'discovery_data':{'payload':{'unique_id':'unique','state_topic':topic}}}]}
        self.assertEqual(d.source_topic(info('zigbee2mqtt/bed'),'unique'),'zigbee2mqtt/bed')
        for topic in ['zigbee2mqtt/#','zigbee2mqtt/+/state','zigbee2mqtt/bed/set','zigbee2mqtt/bridge/logging']:
            self.assertIsNone(d.source_topic(info(topic),'unique'))
        self.assertIsNone(d.source_topic(info('zigbee2mqtt/bed'),'other'))

    def test_sample_buffer_is_bounded_and_retained_messages_stay_marked(self):
        manager=type('Manager',(),{})();worker=d.DeviceDiagnostics(manager,clock=lambda:0);worker.job={'id':'test','expires_at':'1970-01-01T00:00:30Z','management_id':'a'*32}
        for _ in range(500):worker.append('test','zigbee2mqtt',{'presence':False},True)
        self.assertEqual(len(worker.samples),40);self.assertTrue(worker.samples[0]['retained']);self.assertEqual(worker.sequence,40)

    def test_opt_out_or_missing_response_discards_pending_data(self):
        worker=d.DeviceDiagnostics(type('Manager',(),{})());worker.job={'id':'test'};worker.samples=[{'private':'temporary'}]
        worker.accept(None,self.options(),{});self.assertIsNone(worker.job);self.assertEqual(worker.samples,[])

    def collector(self, wrong_identity=False, opted_out=False):
        options=self.options();job=self.payload()['capture'];now=d.expiry('2026-11-18T00:00:00Z');clock=[now];calls=[];sent=[]
        class Connector:
            credentials={'bridge_id':'synthetic','token':'local-only'}
            allowlist=set(options['entity_ids'])
            def stopping(self):return False
            def ha(self,path,timeout=5):
                calls.append(path)
                if path=='states':return [{'entity_id':'number.sensitivity','state':'120'},{'entity_id':'sensor.other_person','state':'never-share'}]
                return {'state':'off','attributes':{'private':'never-share'},'last_changed':'2026-11-18T00:00:00Z'}
        c=Connector()
        class Manager:
            connector=c
            stopped=threading.Event()
            def options(self):return {**options,'managed_settings':False} if opted_out else options
        class Socket:
            received=0
            def send(self,payload):sent.append(json.loads(payload))
            def recv(self,timeout):
                self.received+=1
                clock[0]=now+self.received
                if self.received==1:return json.dumps({'id':4,'type':'result','success':True})
                if self.received>2:
                    clock[0]=now+31
                    raise TimeoutError()
                return json.dumps({'id':4,'event':{'topic':'zigbee2mqtt/bed','retain':True,'payload':json.dumps({'presence':True,'motion_sensitivity':'high','password':'never-share'})}})
            def close(self):pass
        class Home:
            def socket(self):return Socket()
            def auth(self,ws):pass
            def query(self,ws,ident,kind,**args):
                if kind=='config/entity_registry/list':return [{'entity_id':'binary_sensor.bed','device_id':'b'*32,'unique_id':'wrong' if wrong_identity else 'bed_presence_zigbee2mqtt','platform':'mqtt'},{'entity_id':'number.sensitivity','device_id':'b'*32,'unique_id':'bed_absence_delay_timer_zigbee2mqtt'}]
                if kind=='config/device_registry/list':return [{'id':'b'*32,'sw_version':'0.0.0_6542','manufacturer':'Aqara','model':'FP300'}]
                return {'entities':[{'discovery_data':{'payload':{'unique_id':'bed_presence_zigbee2mqtt','state_topic':'zigbee2mqtt/bed'}}}]}
        worker=d.DeviceDiagnostics(Manager(),home_factory=lambda _:Home(),clock=lambda:clock[0]);worker.job={**job,'management_id':options['management_id']}
        return worker,job,options,c,calls,sent,clock,now

    def test_collector_reads_one_exact_device_and_never_dispatches_a_write(self):
        worker,job,options,c,calls,sent,clock,now=self.collector()
        worker.run(job['id'],options,c.credentials)
        self.assertEqual([s['source'] for s in worker.samples],['settings','home_assistant','zigbee2mqtt'])
        self.assertEqual(worker.samples[1]['data']['state'],'off');self.assertTrue(worker.samples[2]['data']['presence']);self.assertTrue(worker.samples[2]['retained'])
        self.assertEqual(worker.snapshot['firmware'],'0.0.0_6542');self.assertTrue(worker.snapshot['mqtt_available'])
        self.assertEqual(calls,['states','states/binary_sensor.bed']);self.assertEqual(sent,[{'id':4,'type':'mqtt/subscribe','topic':'zigbee2mqtt/bed','qos':0}])
        self.assertNotIn('never-share',json.dumps(worker.samples))

    def test_changed_identity_stops_before_reading_sensor_values(self):
        worker,job,options,c,calls,*_=self.collector(wrong_identity=True);worker.run(job['id'],options,c.credentials)
        self.assertEqual(calls,[]);self.assertEqual(worker.samples,[]);self.assertEqual(worker.status,'unavailable')

    def test_local_opt_out_stops_before_any_device_query(self):
        worker,job,options,c,calls,sent,*_=self.collector(opted_out=True);worker.run(job['id'],options,c.credentials)
        self.assertEqual(calls,[]);self.assertEqual(sent,[]);self.assertEqual(worker.samples,[])


if __name__=='__main__':unittest.main()
