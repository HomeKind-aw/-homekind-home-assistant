import copy
import hashlib
import json
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'homekind_bridge'))
from network_setup import NetworkSetup, NetworkHome

RID = '71000000-0000-4000-8000-000000000001'
SID = '71000000-0000-4000-8000-000000000002'
MID = 'a' * 32
KEY = 'b' * 64
FLOW = {'type': 'form', 'flow_id': 'local_flow_1', 'handler': 'sonos', 'step_id': 'confirm', 'data_schema': [], 'errors': {}}


class Manager:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.RLock()
        self.settings_value = {'managed_settings': True, 'management_id': MID, 'entity_ids': ['sensor.existing']}
        self.directive = {'id': RID, 'session_id': SID, 'key': KEY, 'management_id': MID, 'expires_at': '2026-10-26T00:02:00Z', 'status': 'queued'}
        self.calls, self.paired_value, self.acknowledge = [], True, True
        self.allow, self.authorize_error = True, False

    def options(self):
        return copy.deepcopy(self.settings_value)

    def paired(self):
        return self.paired_value

    def request(self, url, token, body, timeout):
        self.calls.append(copy.deepcopy(body))
        if body['operation'] == 'poll':
            return {'directive': copy.deepcopy(self.directive)}
        if body['operation'] == 'authorize':
            if self.allow:
                self.directive['status'] = 'connecting'
            if self.authorize_error:
                raise TimeoutError('Lost authorisation response')
            return {'authorised': self.allow}
        if body['operation'] == 'ack':
            if self.acknowledge:
                self.directive = None
            return {'accepted': self.acknowledge}
        raise AssertionError('Unsupported operation')


class Home:
    def __init__(self):
        self.flow, self.calls, self.error = copy.deepcopy(FLOW), [], False

    def locate(self, key):
        self.calls.append(('read', key))
        return copy.deepcopy(self.flow)

    confirm_only = staticmethod(NetworkHome.confirm_only)

    def confirm(self, flow):
        self.calls.append(('write', copy.deepcopy(flow)))
        if self.error:
            raise TimeoutError('Lost connection response')
        return 'connected'


class NetworkSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.m, self.h = Manager(self.tmp.name), Home()
        self.client = SimpleNamespace(credentials={'token': 'synthetic-test-token', 'bridge_id': SID})
        self.worker = NetworkSetup(self.m, lambda client: self.h, clock=lambda: 1792972800)

    def step(self):
        self.worker.step(self.client, self.m.options())

    def outcome(self):
        return next(x['payload']['status'] for x in reversed(self.m.calls) if x['operation'] == 'ack')

    def test_explicit_native_confirmation_uses_no_brand_configuration_or_secrets(self):
        self.step()
        self.assertEqual(self.outcome(), 'connected')
        self.assertEqual([x[0] for x in self.h.calls], ['read', 'write'])
        self.assertEqual(self.m.options()['entity_ids'], ['sensor.existing'])
        for call in self.m.calls:
            self.assertEqual(set(call), {'bridge_id', 'operation', 'payload'})
            self.assertNotIn('local_flow_1', json.dumps(call))
            self.assertNotIn('data_schema', json.dumps(call))
            self.assertNotIn('handler', json.dumps(call))

    def test_credential_qr_external_menu_and_progress_steps_require_a_person(self):
        for change in [{'data_schema': [{'name': 'password'}]}, {'type': 'external', 'url': 'https://account.invalid'}, {'type': 'menu'}, {'type': 'progress'}, {'description_placeholders': {'qr_code': 'secret'}}, {'description_placeholders': {'account_link': 'secret'}}, {'step_id': 'user'}, {'errors': {'base': 'invalid_auth'}}]:
            with self.subTest(change=change):
                self.assertFalse(NetworkHome.confirm_only({**FLOW, **change}))
        self.h.flow['data_schema'] = [{'name': 'password'}]
        self.step()
        self.assertEqual(self.outcome(), 'approval_required')
        self.assertEqual([x[0] for x in self.h.calls], ['read'])
        self.assertFalse(any(x['operation'] == 'authorize' for x in self.m.calls))

    def test_unknown_and_expired_directives_never_touch_home_assistant(self):
        for change in [{'id': 'invalid'}, {'key': 'http://untrusted'}, {'management_id': 'c' * 32}, {'expires_at': '2026-10-25T23:59:00Z'}, {'expires_at': '2026-10-26T00:20:00Z'}, {'session_id': 'invalid'}]:
            with self.subTest(change=change):
                self.m.directive.update(change)
                self.step()
                self.assertEqual(self.h.calls, [])
                self.m.directive = Manager(self.tmp.name).directive

    def test_opt_out_and_lost_pairing_do_not_publish_or_configure(self):
        self.m.settings_value['managed_settings'] = False
        self.step()
        self.m.settings_value['managed_settings'] = True
        self.m.paired_value = False
        self.step()
        self.assertEqual(self.m.calls, [])

    def test_revocation_after_inspection_stops_the_configuration_write(self):
        def locate(key):
            self.m.settings_value['management_id'] = 'c' * 32
            return copy.deepcopy(FLOW)
        self.h.locate = locate
        self.step()
        self.assertFalse(any(x['operation'] == 'authorize' for x in self.m.calls))
        self.assertFalse(any(x[0] == 'write' for x in self.h.calls))

    def test_denied_authorisation_never_writes(self):
        self.m.allow = False
        self.step()
        self.assertEqual(self.outcome(), 'unavailable')
        self.assertEqual([x[0] for x in self.h.calls], ['read'])

    def test_ambiguous_authorisation_never_replays(self):
        self.m.authorize_error = True
        self.step()
        self.assertEqual(self.outcome(), 'uncertain')
        self.assertEqual([x[0] for x in self.h.calls], ['read'])
        self.step()
        self.assertEqual([x[0] for x in self.h.calls], ['read'])

    def test_lost_configuration_response_and_receipt_are_never_replayed(self):
        self.m.acknowledge, self.h.error = False, True
        self.step()
        self.assertEqual(self.outcome(), 'uncertain')
        self.worker = NetworkSetup(self.m, lambda client: self.h, clock=lambda: 1792972800)
        self.step()
        self.assertEqual([x[0] for x in self.h.calls], ['read', 'write'])
        self.assertEqual(self.outcome(), 'uncertain')

    def test_a_lost_success_receipt_is_recovered_without_another_write(self):
        self.m.acknowledge = False
        self.step()
        self.worker = NetworkSetup(self.m, lambda client: self.h, clock=lambda: 1792972800)
        self.m.acknowledge = True
        self.step()
        self.assertEqual([x[0] for x in self.h.calls], ['read', 'write'])
        self.assertEqual(self.outcome(), 'connected')

    def test_crash_after_authorisation_is_uncertain_without_a_write(self):
        self.worker.saved = {'id': RID, 'management_id': MID, 'stage': 'dispatched'}
        self.worker.save()
        self.m.directive['status'] = 'connecting'
        self.worker = NetworkSetup(self.m, lambda client: self.h, clock=lambda: 1792972800)
        self.step()
        self.assertEqual(self.h.calls, [])
        self.assertEqual(self.outcome(), 'uncertain')

    def test_removed_discovery_has_no_side_effect(self):
        self.h.flow = None
        self.step()
        self.assertEqual(self.outcome(), 'not_found')
        self.assertFalse(any(x[0] == 'write' for x in self.h.calls))

    def test_corrupt_optional_setup_status_cannot_disable_ordinary_discovery(self):
        from device_onboarding import DeviceOnboarding
        self.worker.path.write_text('not-json')
        onboarding = DeviceOnboarding(self.m)
        self.assertIsNone(onboarding.network_setup)
        self.assertFalse(onboarding.available)

    def test_real_local_api_uses_the_locally_discovered_id_and_empty_confirmation(self):
        flow = {'flow_id': 'local_flow_1', 'handler': 'sonos', 'context': {'source': 'ssdp'}}
        key = hashlib.sha256(b'sonos\0local_flow_1').hexdigest()
        calls = []
        client = SimpleNamespace(ha=lambda path, data=None, timeout=None: calls.append((path, data)) or (copy.deepcopy(FLOW) if data is None else {'type': 'create_entry', 'data': {'token': 'never-leave-home'}}))
        home = NetworkHome(client, connect=lambda *args, **kwargs: None)
        class Socket:
            def __enter__(self): return self
            def __exit__(self, *args): pass
        home.socket, home.auth, home.query = lambda: Socket(), lambda ws: None, lambda *args: [flow]
        details = home.locate(key)
        self.assertTrue(home.confirm_only(details))
        self.assertEqual(home.confirm(details), 'connected')
        self.assertEqual(calls, [('config/config_entries/flow/local_flow_1', None), ('config/config_entries/flow/local_flow_1', {})])
        flow['context']['source'] = 'user'
        calls.clear()
        self.assertIsNone(home.locate(key))
        self.assertEqual(calls, [])
        flow['context']['source'] = 'bluetooth'
        self.assertIsNone(home.locate(key))

    def test_untrusted_local_ids_and_changed_handlers_cannot_be_confirmed(self):
        for ident, handler in [('bad/../flow', 'sonos'), ('local_flow_1', 'different')]:
            flow = {'flow_id': ident, 'handler': 'sonos', 'context': {'source': 'ssdp'}}
            key = hashlib.sha256(('sonos\0' + ident).encode()).hexdigest()
            client = SimpleNamespace(ha=lambda *args, **kwargs: {**FLOW, 'handler': handler})
            home = NetworkHome(client)
            class Socket:
                def __enter__(self): return self
                def __exit__(self, *args): pass
            home.socket, home.auth, home.query = lambda: Socket(), lambda ws: None, lambda *args: [flow]
            self.assertIsNone(home.locate(key))


if __name__ == '__main__':
    unittest.main()
