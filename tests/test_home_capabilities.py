import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

APP = Path(__file__).parents[1] / "homekind_bridge"
sys.path.insert(0, str(APP))
import bridge
import device_capabilities as caps
import managed_settings

NOW = 1800000000


def state(entity='cover.curtains', value='closed', **attrs):
    return {'entity_id': entity, 'state': value, 'attributes': {'friendly_name': 'Test device', **attrs}, 'last_updated': '2027-01-15T08:00:00Z', 'last_changed': '2027-01-15T08:00:00Z', 'context': {'id': value}}


class CapabilityTests(unittest.TestCase):
    def test_environment_and_safety_classes_share_the_existing_numeric_and_binary_privacy_boundary(self):
        for klass in ('absolute_humidity', 'aqi', 'pressure', 'pm1', 'pm4', 'carbon_monoxide', 'gas', 'volume_flow_rate', 'wind_speed', ''):
            s = state('sensor.reading', '12.5', device_class=klass)
            self.assertTrue(bridge.safe_entity(s), klass)
            self.assertTrue(bridge.safe_reading(s), klass)
        for klass in caps.BINARY_CLASSES:
            self.assertTrue(bridge.safe_entity(state('binary_sensor.reading', 'on', device_class=klass)), klass)
        for s in (state('person.resident', 'home'), state('sensor.notes', 'private notes'), state('sensor.metric', '42', device_class='unsupported_health')):
            self.assertFalse(bridge.safe_entity(s))

    def test_covers_expose_only_reported_features_and_access_doors_remain_status_only(self):
        c = state(device_class='curtain', supported_features=1 | 2 | 4 | 8 | 128, current_position=25, current_tilt_position=50, access_token='private')
        self.assertEqual(caps.capabilities(c)['actions'], ['open', 'close', 'stop', 'position', 'tilt'])
        self.assertEqual(caps.action_request(c, 'position', {'value': 75}), ('cover', 'set_cover_position', {'position': 75}))
        self.assertEqual(caps.attributes(c)['position'], 25)
        self.assertNotIn('access_token', json.dumps(bridge.observation(c)))
        for klass in ('garage', 'gate', 'door', 'window'):
            self.assertEqual(caps.capabilities(state(device_class=klass, supported_features=255))['actions'], [])
        with self.assertRaises(ValueError):
            caps.action_request(state(device_class='curtain', supported_features=3), 'stop')

    def test_position_and_tilt_are_integral_bounded_and_not_arbitrary_service_payloads(self):
        c = state(device_class='blind', supported_features=255)
        for parameters in ({'value': 101}, {'value': -1}, {'value': 50.5}, {'value': True}, {'value': '50'}, {'value': 50, 'entity_id': 'cover.other'}, {'service': 'unlock'}):
            with self.assertRaises(ValueError):
                caps.action_request(c, 'position', parameters)

    def test_fan_flags_speed_steps_and_oscillation_types_are_checked(self):
        fan = state('fan.living', 'on', supported_features=1 | 2 | 16 | 32, percentage_step=25, percentage=50, oscillating=True)
        self.assertEqual(caps.capabilities(fan)['actions'], ['on', 'off', 'percentage', 'oscillate'])
        self.assertEqual(caps.action_request(fan, 'percentage', {'value': 75}), ('fan', 'set_percentage', {'percentage': 75}))
        self.assertEqual(caps.action_request(fan, 'oscillate', {'value': False})[2], {'oscillating': False})
        self.assertTrue(caps.action_matches(fan, 'oscillate', {'value': True}))
        three = copy.deepcopy(fan)
        three['attributes']['percentage_step'] = 100 / 3
        self.assertEqual(caps.percentage_values(100 / 3), [0, 33, 66, 100])
        self.assertEqual(caps.action_request(three, 'percentage', {'value': 66})[2], {'percentage': 66})
        with self.assertRaises(ValueError):
            caps.action_request(three, 'percentage', {'value': 67})
        for a, p in [('percentage', {'value': 40}), ('oscillate', {'value': 'true'})]:
            with self.assertRaises(ValueError):
                caps.action_request(fan, a, p)

    def test_climate_uses_actual_modes_units_range_and_step(self):
        c = state('climate.living', 'off', supported_features=1, hvac_modes=['off', 'heat', 'cool', 'malicious'], min_temp=7, max_temp=35, target_temp_step=0.5, temperature_unit='°C', temperature=21)
        self.assertEqual(caps.capabilities(c)['hvac_modes'], ['off', 'heat', 'cool'])
        self.assertEqual(caps.action_request(c, 'temperature', {'value': 22.5}), ('climate', 'set_temperature', {'temperature': 22.5}))
        for a, p in [('temperature', {'value': 36}), ('temperature', {'value': 22.25}), ('hvac', {'value': 'auto'})]:
            with self.assertRaises(ValueError):
                caps.action_request(c, a, p)
        no_unit = copy.deepcopy(c)
        del no_unit['attributes']['temperature_unit']
        self.assertNotIn('temperature', caps.capabilities(no_unit)['actions'])
        offset = copy.deepcopy(c)
        offset['attributes']['min_temp'] = 7.2
        self.assertEqual(caps.action_request(offset, 'temperature', {'value': 7.7})[2], {'temperature': 7.7})

    def test_light_brightness_requires_a_reported_colour_mode(self):
        plain = state('light.living', 'on')
        with self.assertRaises(ValueError):
            caps.action_request(plain, 'brightness', {'value': 50})
        dimmer = state('light.living', 'on', supported_color_modes=['brightness'], brightness=128)
        self.assertEqual(caps.action_request(dimmer, 'brightness', {'value': 50}), ('light', 'turn_on', {'brightness_pct': 50}))
        self.assertEqual(caps.attributes(dimmer)['brightness'], 50)

    def test_expanded_domains_survive_managed_settings_restart_without_enabling_controls(self):
        entities = ['cover.curtains', 'fan.living', 'climate.living', 'lock.front', 'valve.water', 'humidifier.bedroom', 'water_heater.home', 'alarm_control_panel.home', 'siren.home', 'lawn_mower.garden']
        options = managed_settings.normalise({'entity_ids': entities, 'managed_settings': True, 'management_id': 'a' * 32})
        self.assertEqual(options['entity_ids'], entities)
        self.assertTrue(options['read_only'])
        self.assertEqual(managed_settings.normalise(options), options)

    def run_command(self, before, action, params=None, *, fail=False, cloud_service=None, after=None):
        entity = before['entity_id']
        domain, service, fields = caps.action_request(before, action, params)
        calls = []
        count = 0
        def request(url, token, data=None, **kwargs):
            nonlocal count
            calls.append((url, copy.deepcopy(data)))
            if url.endswith('/authorize'):
                return {'authorised': True, 'entity_id': entity, 'domain': domain, 'service': cloud_service or service, 'desired': action, 'parameters': params or {}, 'expires_at': '2027-01-15T08:00:20Z'}
            if url.endswith('/ack'):
                return {}
            if url.endswith('/services'):
                return [{'domain': domain, 'services': {service: {}}}]
            if '/services/' in url:
                if fail:
                    raise bridge.BridgeError('synthetic lost response')
                return []
            if '/states/' in url:
                count += 1
                return before if count <= 2 else after or before
            raise AssertionError(url)
        with tempfile.TemporaryDirectory() as directory:
            bridge.atomic_json(Path(directory) / 'credentials.json', {'bridge_id': 'synthetic-bridge', 'token': 'a' * 64})
            c = bridge.Connector({'entity_ids': [entity], 'read_only': False}, directory, request=request, clock=lambda: NOW, sleeper=lambda _: None)
            c.tracked = [{'entity_id': entity, 'control_enabled': True}]
            c.execute({'id': 'synthetic-command', 'lease_id': 'synthetic-lease', 'entity_id': entity})
        return calls

    def test_actual_cover_dispatch_uses_exact_position_and_fresh_state_confirmation(self):
        before = state(device_class='curtain', supported_features=15, current_position=0)
        after = state(value='open', device_class='curtain', supported_features=15, current_position=60)
        after['last_updated'] = '2027-01-15T08:00:01Z'
        calls = self.run_command(before, 'position', {'value': 60}, after=after)
        dispatch = [(url, data) for url, data in calls if '/services/' in url]
        self.assertEqual(len(dispatch), 1)
        self.assertTrue(dispatch[0][0].endswith('/services/cover/set_cover_position'))
        self.assertEqual(dispatch[0][1], {'entity_id': 'cover.curtains', 'position': 60})
        self.assertEqual(calls[-1][1]['status'], 'confirmed')

    def test_stop_and_uncertain_transport_never_claim_completion_or_repeat_dispatch(self):
        before = state(value='opening', device_class='curtain', supported_features=15, current_position=30)
        for fail in (False, True):
            calls = self.run_command(before, 'stop', fail=fail)
            self.assertEqual(sum('/services/' in url for url, _ in calls), 1)
            self.assertEqual(calls[-1][1]['status'], 'unconfirmed')

    def test_cloud_cannot_change_the_local_service_mapping(self):
        before = state(device_class='curtain', supported_features=15, current_position=0)
        calls = self.run_command(before, 'open', cloud_service='unlock')
        self.assertEqual(sum('/services/' in url for url, _ in calls), 0)
        self.assertEqual(calls[-1][1]['status'], 'failed')

    def test_temperature_unit_is_loaded_locally_and_credentials_never_leave_with_attributes(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            def request(url, token, data=None, **kwargs):
                calls.append(url)
                return {'unit_system': {'temperature': '°F'}}
            c = bridge.Connector({'entity_ids': ['climate.home']}, directory, request=request, clock=lambda: NOW)
            s = c.enrich(state('climate.home', 'heat', supported_features=1, hvac_modes=['off', 'heat'], min_temp=50, max_temp=90, target_temp_step=1))
            self.assertEqual(caps.capabilities(s)['temperature_unit'], '°F')
            self.assertEqual(len(calls), 1)
            self.assertEqual(c.enrich(s), s)

    def test_richer_multibyte_observations_are_batched_without_dropping_entities(self):
        raw = [state('climate.' + str(i).zfill(100), 'heat', friendly_name='温' * 80, supported_features=1, hvac_modes=['off', 'heat', 'cool', 'heat_cool', 'auto', 'dry', 'fan_only'], min_temp=-99, max_temp=199, target_temp_step=0.01, temperature_unit='°F', current_temperature=70, temperature=72, current_humidity=40, hvac_action='heating') for i in range(250)]
        catalogue = [{**bridge.catalog_entry(s), **{key: 'a' * 200 for key in ('ha_unique_id', 'ha_platform', 'ha_device_id', 'ha_area_id', 'ha_area_name', 'ha_entity_category')}} for s in raw]
        remaining = [{**bridge.observation(s), 'observed_at': '2027-01-15T08:00:00Z', 'event_id': str(i).zfill(64)} for i, s in enumerate(raw)]
        sent = []
        while remaining:
            payload = bridge.bounded_sync({'catalog': catalogue, 'states': remaining, 'ha_connected': True, 'version': bridge.VERSION})
            self.assertEqual(len(payload['catalog']), 250)
            self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()), 320000)
            self.assertTrue(payload['states'])
            sent.extend(s['entity_id'] for s in payload['states'])
            remaining = remaining[len(payload['states']):]
        self.assertEqual(sent, [s['entity_id'] for s in raw])
