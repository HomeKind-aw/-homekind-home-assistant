import copy
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

APP = Path(__file__).parents[1] / 'bridge' / 'homekind_bridge'
sys.path.insert(0, str(APP))
import device_onboarding as ob
import managed_settings
from bridge import atomic_json, BridgeError
from setup_server import SetupManager

SID = '71000000-0000-4000-8000-000000000001'
DID = '71000000-0000-4000-8000-000000000002'
STATE = {'entity_id': 'sensor.kitchen_temperature', 'state': '21.8', 'attributes': {'friendly_name': 'Kitchen temperature', 'device_class': 'temperature', 'unit_of_measurement': '°C', 'access_token': 'never-upload', 'entity_picture': 'https://private.invalid'}}


class Home:
    def __init__(self, client):
        self.c = client
        self.metadata = {}
        self.routes = {'zha': {'kind': 'zha', 'key': 'zha', 'name': 'Zigbee'}}
        self.excluded_devices = set()
        self.calls = []

    def inspect(self):
        return []

    def pair(self, route, duration):
        self.calls.append(duration)


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = SetupManager(self.temp.name)
        self.options = managed_settings.normalise({'entity_ids': ['light.existing'], 'managed_settings': True, 'management_id': 'a' * 32, 'read_only': False, 'live_camera_entity_ids': []})
        atomic_json(self.manager.settings, self.options)
        atomic_json(self.manager.credentials, {'bridge_id': SID, 'token': 'synthetic-' + 'c' * 64})
        self.credentials = self.manager.credentials.read_bytes()
        self.now = 1800000000
        self.authorise = True
        self.reports = []
        self.manager.request = self.request
        self.worker = ob.DeviceOnboarding(self.manager, Home, clock=lambda: self.now)
        self.worker.client = SimpleNamespace(credentials=json.loads(self.credentials))
        self.worker.home = Home(self.worker.client)
        self.worker.rows = ob.candidates([STATE])
        self.directive = {'session_id': SID, 'management_id': 'a' * 32, 'mode': 'zigbee', 'adapter': 'zha', 'phase': 'searching', 'expires_at': '2027-01-15T08:05:00Z', 'pair_until': '2027-01-15T08:01:30Z', 'revision': 0}

    def tearDown(self):
        self.manager.stopped.set()
        self.temp.cleanup()

    def request(self, url, token, data, **kwargs):
        self.reports.append((url, copy.deepcopy(data)))
        if url.endswith('/onboarding-authorize'):
            return {'authorised': self.authorise}
        return {'directive': None, 'receipt_accepted': False}

    def approved(self):
        row = self.worker.rows[0]
        return {**self.directive, 'phase': 'approved', 'revision': 1, 'approved': [{'entity_id': row['entity_id'], 'fingerprint': row['fingerprint'], 'device_id': DID}]}

    def test_discovery_is_metadata_only_and_supported(self):
        rows = ob.candidates([STATE, {'entity_id': 'lock.front', 'state': 'unlocked'}, {'entity_id': 'weather.home', 'state': 'sunny'}], {'sensor.kitchen_temperature': {'ha_device_id': 'device', 'password': 'never'}}, set())
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['new'])
        self.assertEqual(rows[0]['ha_device_id'], 'device')
        self.assertFalse({'state', 'attributes', 'access_token', 'password', 'entity_picture'} & rows[0].keys())
        self.assertNotIn('21.8', json.dumps(rows))
        self.assertEqual(rows[0]['fingerprint'], ob.fingerprint(rows[0]))

    def test_z2m_uses_exact_local_discovery_and_refuses_guessed_topics(self):
        cfg = {'device': {'manufacturer': 'Zigbee2MQTT', 'model': 'Bridge'}, 'command_topic': 'my_home/bridge/request/permit_join', 'state_topic': 'my_home/bridge/info', 'payload_off': '{"time":0}'}
        info = lambda c: {'entities': [{'discovery_data': {'payload': c}}]}
        route = ob.z2m_route(info(cfg))
        self.assertEqual(route['response_topic'], 'my_home/bridge/response/permit_join')
        for change in ({'command_topic': 'bad/#/bridge/request/permit_join'}, {'state_topic': 'another/bridge/info'}, {'payload_off': 'false'}, {'device': {'manufacturer': 'Other', 'model': 'Bridge'}}):
            self.assertIsNone(ob.z2m_route(info({**cfg, **change})))
        for change in ({'response_topic': 'other/bridge/response/permit_join'}, {'key': 'z2m_' + 'a' * 24}):
            with self.assertRaises(BridgeError):
                ob.LocalHome(SimpleNamespace(), connect=lambda *a, **k: self.fail('must not connect')).pair({**route, **change}, 90)

    def test_pairing_is_bounded_and_never_repeated_after_dispatch_or_restart(self):
        self.worker.act(self.directive, self.options)
        self.worker.act(self.directive, self.options)
        self.assertEqual(self.worker.home.calls, [90])
        restarted = ob.DeviceOnboarding(self.manager, Home, clock=lambda: self.now)
        restarted.home = self.worker.home
        restarted.client = self.worker.client
        restarted.rows = self.worker.rows
        restarted.act(self.directive, self.options)
        self.assertEqual(self.worker.home.calls, [90])
        self.now += 91
        restarted.act(self.directive, self.options)
        self.assertEqual(self.worker.home.calls, [90, 0])

    def test_denied_or_expired_requests_never_open_pairing(self):
        self.authorise = False
        self.worker.act(self.directive, self.options)
        self.now += 301
        self.authorise = True
        self.worker.act(self.directive, self.options)
        self.assertEqual(self.worker.home.calls, [])

    def test_uncertain_physical_dispatch_is_not_retried(self):
        self.worker.home.pair = lambda *args: (_ for _ in ()).throw(TimeoutError())
        self.worker.act(self.directive, self.options)
        self.worker.act(self.directive, self.options)
        self.assertEqual(self.worker.error, 'pairing_not_confirmed')
        self.assertTrue(self.worker.saved['pair_dispatched'])
        self.assertEqual(sum(url.endswith('/onboarding-authorize') for url, _ in self.reports), 1)

    def test_only_confirmed_candidates_append_without_changing_features_or_credentials(self):
        self.worker.act(self.approved(), self.options)
        saved = self.manager.options()
        self.assertEqual(saved['entity_ids'], ['light.existing', 'sensor.kitchen_temperature'])
        self.assertNotEqual(saved['management_id'], self.options['management_id'])
        self.assertEqual(saved['applied_revision'], 0)
        for key in self.options.keys() - {'entity_ids', 'management_id', 'applied_revision'}:
            self.assertEqual(saved[key], self.options[key])
        self.assertEqual(self.manager.credentials.read_bytes(), self.credentials)
        self.assertEqual(self.worker.saved['receipt']['entity_ids'], ['sensor.kitchen_temperature'])
        self.assertTrue(self.manager.changed.is_set())

    def test_no_append_for_stale_identity_opt_out_or_revoked_permission(self):
        cases = ['fingerprint', 'optout', 'permission']
        for case in cases:
            directive = self.approved()
            options = copy.deepcopy(self.options)
            if case == 'fingerprint':
                directive['approved'][0]['fingerprint'] = '0' * 64
            elif case == 'optout':
                options['managed_settings'] = False
            else:
                self.authorise = False
            self.worker.act(directive, options)
            self.assertEqual(self.manager.options(), self.options)

    def test_local_edit_during_authorisation_takes_priority(self):
        original = self.manager.request
        def changed(*args, **kwargs):
            atomic_json(self.manager.settings, {**self.options, 'management_id': 'b' * 32})
            return original(*args, **kwargs)
        self.manager.request = changed
        self.worker.act(self.approved(), self.options)
        self.assertEqual(self.manager.options()['entity_ids'], ['light.existing'])
        self.assertEqual(self.worker.error, 'settings_changed')

    def test_interrupted_receipt_save_recovers_without_reapplying_settings(self):
        real_save = self.worker.save
        def fail_receipt():
            if self.worker.saved.get('receipt'):
                raise OSError('synthetic crash after settings write')
            real_save()
        self.worker.save = fail_receipt
        with self.assertRaises(OSError):
            self.worker.act(self.approved(), self.options)
        saved = self.manager.options()
        restarted = ob.DeviceOnboarding(self.manager, Home, clock=lambda: self.now)
        fake = SimpleNamespace(credentials=json.loads(self.credentials), ha=lambda *a: [STATE])
        with patch.object(ob, 'Connector', return_value=fake):
            restarted.step()
        self.assertEqual(self.manager.options(), saved)
        self.assertEqual(restarted.saved['receipt']['new_management_id'], saved['management_id'])
        self.assertEqual(self.reports[-1][1]['candidates'], [])

    def test_no_device_catalogue_is_uploaded_outside_an_explicit_session(self):
        fake = SimpleNamespace(credentials=json.loads(self.credentials), ha=lambda *a: [STATE])
        with patch.object(ob, 'Connector', return_value=fake):
            self.worker.step()
        self.assertEqual(self.reports[-1][1]['candidates'], [])

    def test_cancelled_receipt_rolls_back_only_newly_appended_entities(self):
        self.worker.act(self.approved(), self.options)
        fake = SimpleNamespace(credentials=json.loads(self.credentials), ha=lambda *a: [STATE])
        self.manager.request = lambda *a, **k: {'directive': None, 'receipt_accepted': True, 'receipt_status': 'cancelled'}
        with patch.object(ob, 'Connector', return_value=fake):
            self.worker.step()
        self.assertEqual(self.manager.options()['entity_ids'], self.options['entity_ids'])
        self.assertFalse(self.manager.options()['read_only'])
        self.assertEqual(self.manager.credentials.read_bytes(), self.credentials)

    def test_z2m_requires_the_matching_live_transaction_before_confirmation(self):
        cfg = {'device': {'manufacturer': 'Zigbee2MQTT', 'model': 'Bridge'}, 'command_topic': 'my_home/bridge/request/permit_join', 'state_topic': 'my_home/bridge/info', 'payload_off': '{"time":0}'}
        route = ob.z2m_route({'entities': [{'discovery_data': {'payload': cfg}}]})
        for retained, transaction in [(False, 'matching'), (True, 'matching'), (False, 'other')]:
            class Socket:
                def __init__(self):
                    self.messages = [{'type': 'auth_required'}, {'type': 'auth_ok'}, {'id': 1, 'type': 'result', 'success': True}]
                    self.sent = []
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
                def send(self, data):
                    self.sent.append(json.loads(data))
                def recv(self, **kwargs):
                    if self.messages:
                        return json.dumps(self.messages.pop(0))
                    if not hasattr(self, 'answer'):
                        self.answer = True
                        return json.dumps({'type': 'event', 'id': 1, 'event': {'topic': route['response_topic'], 'retain': retained, 'payload': json.dumps({'status': 'ok', 'transaction': transaction, 'data': {'time': 90}})}})
                    raise TimeoutError()
            ws = Socket()
            calls = []
            client = SimpleNamespace(supervisor=True, ha_token='synthetic-local-token', ha=lambda path, data: calls.append((path, data)))
            local = ob.LocalHome(client, connect=lambda *a, **k: ws)
            with patch.object(ob.secrets, 'token_hex', return_value='matching'):
                if not retained and transaction == 'matching':
                    local.pair(route, 90)
                else:
                    with self.assertRaises((BridgeError, TimeoutError)):
                        local.pair(route, 90)
            self.assertEqual(calls[0][0], 'services/mqtt/publish')
            self.assertFalse(calls[0][1]['retain'])
            self.assertEqual(json.loads(calls[0][1]['payload'])['time'], 90)
            self.assertEqual(ws.sent[-1]['topic'], route['response_topic'])

    def test_local_pairing_rejects_unbounded_durations(self):
        local = ob.LocalHome(SimpleNamespace(ha=lambda *a: self.fail('must not dispatch')))
        for duration in (91, -1, True, '90'):
            with self.assertRaises(BridgeError):
                local.pair({'kind': 'zha', 'key': 'zha'}, duration)
