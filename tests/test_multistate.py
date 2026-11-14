import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

root = Path(__file__).parents[1]
source = root / 'bridge/homekind_bridge'
sys.path.insert(0, str(source if source.exists() else root / 'homekind_bridge'))
import bridge
from device_onboarding import candidates
from multistate import OBJECT_ACTIONS, object_action, object_candidates


class MultiStateTests(unittest.TestCase):
    def row(self, entity, value, attrs=None, at='2026-11-14T10:00:00+00:00'):
        return {'entity_id': entity, 'state': value, 'attributes': attrs or {}, 'last_updated': at, 'last_changed': at}

    def event(self, action='movement', at='2026-11-14T10:00:00+00:00'):
        return self.row('event.p100_action', at, {'event_types': sorted(OBJECT_ACTIONS), 'event_type': action}, at)

    def test_orientation_is_a_bounded_enum_instead_of_a_number(self):
        for value in ('face_up', 'face_down', 'vertical', 'tilt', 'unknown', 'unavailable'):
            row = self.row('sensor.p100_orientation', value, {'device_class': 'enum'})
            self.assertTrue(bridge.safe_entity(row))
            self.assertTrue(bridge.safe_reading(row))
            self.assertEqual(bridge.observation(row)['device_class'], 'object_orientation')
        self.assertFalse(bridge.safe_reading(self.row('sensor.p100_orientation', 'unlock')))
        self.assertFalse(bridge.safe_entity(self.row('sensor.unsupported', 'face_up', {'device_class': 'enum'})))

    def test_object_actions_keep_their_meaning_and_reject_unadvertised_types(self):
        for action in OBJECT_ACTIONS:
            row = self.event(action)
            self.assertTrue(bridge.safe_entity(row))
            self.assertTrue(bridge.safe_reading(row))
            self.assertEqual(bridge.observation(row)['event_type'], action)
            self.assertEqual(bridge.observation(row)['device_class'], 'object_action')
        self.assertFalse(bridge.safe_reading(self.event('turn_on')))
        self.assertFalse(bridge.safe_entity(self.row('event.other', 'unknown', {'event_types': ['movement', 'turn_on']})))
        ordinary = self.row('event.button', 'unknown', {'device_class': 'button', 'event_types': ['single', 'double']})
        self.assertEqual(bridge.reading_class(ordinary), 'button')

    def test_discovery_does_not_add_sources_or_treat_detection_switches_as_events(self):
        rows = [self.event(), self.row('sensor.p100_orientation', 'face_up'), self.row('switch.p100_movement_detection', 'on')]
        before = json.dumps(rows, sort_keys=True)
        found = candidates(rows, {r['entity_id']: {'ha_device_id': 'physical-p100'} for r in rows})
        self.assertEqual(len(found), 3)
        self.assertEqual([x['device_class'] for x in found if x['entity_id'].startswith('event.')], ['object_action'])
        self.assertEqual([x['device_class'] for x in found if x['entity_id'].startswith('switch.')], [''])
        self.assertTrue(all(len(x['fingerprint']) == 64 for x in found))
        self.assertEqual(before, json.dumps(rows, sort_keys=True))

    def test_native_mqtt_source_uses_verified_device_actions_without_experimental_entities(self):
        battery = self.row('sensor.0x1234_battery', '100')
        metadata = {battery['entity_id']: {'ha_device_id': 'p100-device', 'ha_platform': 'mqtt', 'ha_model': 'DWZTCGQ11LM'}}
        offered = candidates([battery], metadata)
        actions = [r for r in offered if r['device_class'] == 'object_action']
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['ha_device_id'], 'p100-device')
        self.assertEqual(object_candidates([battery], '2026-11-14T10:00:00Z'), [])
        selected = object_candidates([battery], '2026-11-14T10:00:00Z', allowed={actions[0]['entity_id']})
        self.assertEqual(len(selected), 1)
        triggers = [{'device_id': 'p100-device', 'domain': 'mqtt', 'platform': 'device', 'type': 'action', 'subtype': 'movement'}]
        info = {'triggers': [{'discovery_data': {'payload': {'type': 'action', 'subtype': 'movement', 'topic': 'zigbee2mqtt/p100/action', 'payload': 'movement'}}}]}
        self.assertEqual(bridge.mqtt_button_bindings(info, triggers, 'p100-device', normalise=object_action)[0]['action'], 'movement')
        self.assertEqual(bridge.mqtt_button_bindings(info, triggers, 'another-device', normalise=object_action), [])
        info['triggers'][0]['discovery_data']['payload']['topic'] = 'zigbee2mqtt/#'
        self.assertEqual(bridge.mqtt_button_bindings(info, triggers, 'p100-device', normalise=object_action), [])

    def test_selected_rapid_events_survive_retry_and_outbox_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            options = {'entity_ids': ['event.p100_action'], 'read_only': True}
            c = bridge.Connector(options, directory, request=lambda *a, **kw: {}, clock=lambda: 1794650405)
            c.tracked = [{'entity_id': 'event.p100_action', 'kind': 'other'}]
            for i, action in enumerate(('movement', 'vibration', 'triple_tap')):
                row = self.event(action, '2026-11-14T10:00:0%d+00:00' % i)
                c.accept_state(row)
                c.accept_state(row)
            self.assertEqual(len(c.pending), 3)
            c.accept_state(self.row('sensor.p100_orientation', 'face_up'))
            self.assertEqual(len(c.pending), 3)
            restored = bridge.Connector(options, directory, request=lambda *a, **kw: {}, clock=lambda: 1794650405)
            self.assertEqual(len(restored.pending), 3)
            self.assertEqual({r['event_type'] for r in restored.pending.values()}, {'movement', 'vibration', 'triple_tap'})


if __name__ == '__main__':
    unittest.main()
