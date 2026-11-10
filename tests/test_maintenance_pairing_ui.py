import http.client
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlencode
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homekind_maintenance.maintenance import Companion, MaintenanceError, CLOUD
from homekind_maintenance.recovery_ui import RecoveryServer

CODE = '74000000-0000-4000-8000-000000000099.' + 'a' * 64

class Supervisor:
    def __init__(self, options):
        self.saved = dict(options)
        self.calls = []
        self.reject_code = False
        self.reject_options = False
    def __call__(self, url, token, payload=None, timeout=25):
        self.calls.append((url, payload))
        if url == CLOUD + '/maintenance':
            if self.reject_code:
                raise MaintenanceError('That code has expired. Create a fresh code in HomeKind.')
            assert payload['payload']['ready'] is False
            assert 'result' not in payload['payload']
            return {'command': {'operation': 'update'}}  # Pairing must never dispatch this.
        path = url.removeprefix('http://supervisor')
        if path == '/addons/self/info':
            result = {'slug': 'a123bc45_homekind_maintenance', 'repository': 'a123bc45'}
        elif path == '/addons/a123bc45_homekind_bridge/info':
            result = {'slug': 'a123bc45_homekind_bridge', 'repository': 'a123bc45', 'version': '0.25.0', 'version_latest': '0.25.0', 'state': 'started', 'auto_update': False}
        elif path == '/addons/self/options':
            if self.reject_options:
                raise MaintenanceError('Local choices could not be saved.')
            assert payload['boot'] == 'auto'
            self.saved = dict(payload['options'])
            result = {}
        else:
            raise AssertionError('Unexpected route during pairing: ' + path)
        return {'result': 'ok', 'data': result}

class MaintenancePairingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.options = self.root / 'options.json'
        self.initial = {'pairing_code': '', 'allow_updates': False, 'allow_system_updates': False}
        self.options.write_text(json.dumps(self.initial))
        self.supervisor = Supervisor(self.initial)
        self.companion = Companion(self.root / 'data', call=self.supervisor, clock=lambda: 1000, options_path=self.options)
        self.server = RecoveryServer(('127.0.0.1', 0), self.companion.directory, ingress_ip='127.0.0.1', companion=self.companion)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()
    def request(self, method='GET', path='/', body=None, headers=None):
        c = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        c.request(method, path, body, headers or {})
        r = c.getresponse()
        result = r.status, dict(r.getheaders()), r.read().decode()
        c.close()
        return result
    def form(self):
        status, headers, html = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        return {'csrf': re.search('name="csrf" value="([^"]+)"', html)[1], 'pairing_code': CODE, 'allow_maintenance': 'yes'}
    def post(self, fields):
        return self.request('POST', '/pair', urlencode(fields), {'Content-Type': 'application/x-www-form-urlencoded'})
    def test_unpaired_ingress_opens_with_a_real_paste_field_and_no_setup_side_effects(self):
        _, _, html = self.request()
        self.assertIn('Paste your code here', html)
        self.assertIn('Connect to HomeKind', html)
        self.assertIn('action="pair"', html)
        self.assertNotIn('name="allow_maintenance" value="yes" checked', html)
        self.assertEqual(self.supervisor.calls, [])
        self.assertFalse(self.companion.setup_status()['connected'])
    def test_connect_persists_local_choices_confirms_cloud_and_never_executes_an_update(self):
        form = self.form()
        status, headers, _ = self.post(form)
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], './')
        saved = json.loads(self.options.read_text())
        self.assertEqual(saved, self.supervisor.saved)
        self.assertTrue(saved['allow_updates'])
        self.assertTrue(saved['allow_system_updates'])
        self.assertTrue(self.companion.setup_status()['connected'])
        self.assertEqual(self.companion.data['records'], {})
        self.assertEqual([url for url, p in self.supervisor.calls if p is not None], [CLOUD + '/maintenance', 'http://supervisor/addons/self/options'])
        _, _, html = self.request()
        self.assertIn('You’re connected', html)
        self.assertNotIn(CODE, html)
        restarted = Companion(self.companion.directory, call=self.supervisor, options_path=self.options)
        self.assertEqual(restarted.data['credential'], self.companion.data['credential'])
        self.assertTrue(restarted.options()['allow_system_updates'])
        self.assertFalse(restarted.setup_status()['connected'])  # Fresh report still required.
        self.assertEqual(self.post(form)[0], 403)  # Consumed form cannot re-enable choices later.
    def test_expired_code_keeps_saved_connection_and_choices_and_is_not_echoed(self):
        self.supervisor.reject_code = True
        status, _, html = self.post(self.form())
        self.assertEqual(status, 400)
        self.assertIn('fresh code', html)
        self.assertNotIn(CODE, html)
        self.assertEqual(self.companion.data, {'records': {}})
        self.assertEqual(json.loads(self.options.read_text()), self.initial)
        self.assertFalse(any(url.endswith('/options') for url, _ in self.supervisor.calls))
    def test_explicit_local_choice_is_required_and_no_policy_is_enabled_by_getting_a_code(self):
        form = self.form(); form['allow_maintenance'] = 'no'
        self.assertEqual(self.post(form)[0], 400)
        self.assertEqual(self.supervisor.calls, [])
        self.assertEqual(json.loads(self.options.read_text()), self.initial)
    def test_csrf_duplicates_extra_fields_and_other_routes_are_rejected_before_pairing(self):
        valid = self.form()
        for changes in [{'csrf': 'wrong'}, {'command': 'anything'}, {'pairing_code': [CODE, CODE]}]:
            form = {**valid, **changes}
            if isinstance(form['pairing_code'], list):
                result = self.request('POST', '/pair', urlencode(form, doseq=True), {'Content-Type': 'application/x-www-form-urlencoded'})
            else:
                result = self.post(form)
            self.assertEqual(result[0], 403)
        self.assertEqual(self.request('POST', '/arbitrary', urlencode(valid), {'Content-Type': 'application/x-www-form-urlencoded'})[0], 403)
        self.assertEqual(self.supervisor.calls, [])
    def test_unverified_or_active_operations_keep_their_fence_during_repair(self):
        self.companion.data['active'] = 'operation'
        self.companion.data['records']['operation'] = {'phase': 'unknown'}
        self.assertEqual(self.post(self.form())[0], 400)
        self.assertEqual(self.supervisor.calls, [])
        self.companion.data = {'records': {}}
        (self.companion.directory / 'system-update.json').write_text('{')
        self.assertEqual(self.post(self.form())[0], 400)
        self.assertEqual(self.supervisor.calls, [])
    def test_a_failed_supervisor_save_never_claims_a_paired_connection(self):
        self.supervisor.reject_options = True
        self.assertEqual(self.post(self.form())[0], 400)
        self.assertFalse(self.companion.setup_status()['connected'])
        self.assertNotIn('credential', self.companion.data)
        self.assertEqual(json.loads(self.options.read_text()), self.initial)
    def test_direct_network_post_cannot_be_authorised_by_forged_ingress_headers(self):
        self.server.ingress_ip = '172.30.32.2'
        result = self.request('POST', '/pair', urlencode({'csrf': self.server.csrf, 'pairing_code': CODE, 'allow_maintenance': 'yes'}), {'Content-Type': 'application/x-www-form-urlencoded', 'X-Forwarded-For': '172.30.32.2'})
        self.assertEqual(result[0], 403)
        self.assertEqual(self.supervisor.calls, [])

if __name__ == '__main__':
    unittest.main()
