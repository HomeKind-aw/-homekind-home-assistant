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


if __name__=='__main__':unittest.main()
