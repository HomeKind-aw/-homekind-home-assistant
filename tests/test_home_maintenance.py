import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "homekind_maintenance" / "maintenance.py"
spec = importlib.util.spec_from_file_location('homekind_maintenance', MODULE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
ID = '74000000-0000-4000-8000-000000000020'


class Fake:
    def __init__(self):
        self.calls = []
        self.command = {'id': ID, 'operation': 'update', 'before_version': '0.16.1', 'target_version': '0.17.0', 'backup_id': None, 'recovery_of': None}
        self.info = {'slug': 'a123bc45_homekind_bridge', 'repository': 'a123bc45', 'state': 'started', 'version': '0.16.1', 'version_latest': '0.17.0', 'auto_update': False}
        self.extra = {}
        self.fail_update = False
        self.bad_backup = False
        self.authorised = True

    def __call__(self, url, token, payload=None, timeout=25):
        self.calls.append((url, payload))
        if url == m.CLOUD+'/maintenance':
            if payload['operation'] in ('authorize', 'commit'):
                return {'authorised': self.authorised}
            return {'command': self.command}
        path = url.removeprefix('http://supervisor')
        if path == '/addons/self/info':
            data = {'slug': 'a123bc45_homekind_maintenance', 'repository': 'a123bc45'}
        elif path == '/addons/a123bc45_homekind_bridge/info':
            data = dict(self.info)
        elif path == '/backups/new/partial':
            self.extra = payload['extra']
            data = {'job_id': 'b'*32}
        elif path == '/jobs/'+'b'*32:
            data = {'uuid': 'b'*32, 'done': True, 'errors': [], 'child_jobs': [], 'reference': 'abcd1234'}
        elif path == '/backups/abcd1234/info':
            data = {'slug': 'abcd1234', 'type': 'partial', 'homeassistant': None, 'folders': [], 'addons': [{'slug': self.info['slug'], 'version': '0.16.1'}], 'extra': self.extra, 'protected': True, 'locations': ['.local']}
            if self.bad_backup:
                data['addons'].append({'slug': 'core_mosquitto', 'version': '1.0.0'})
        elif path == '/store/addons/a123bc45_homekind_bridge/update':
            self.info['version'] = '0.17.0'
            if self.fail_update:
                raise m.MaintenanceError('Simulated lost response')
            data = {'job_id': 'c'*32}
        elif path == '/jobs/'+'c'*32:
            data = {'done': True, 'errors': [], 'child_jobs': [], 'reference': self.info['slug']}
        elif path == '/backups/abcd1234/restore/partial':
            self.info['version'] = '0.16.1'
            data = {'job_id': 'd'*32}
        elif path == '/jobs/'+'d'*32:
            data = {'done': True, 'errors': [], 'child_jobs': []}
        else:
            raise AssertionError('Unexpected maintenance route: '+path)
        return {'result': 'ok', 'data': data}


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.options = self.root/'options.json'
        self.options.write_text(json.dumps({'pairing_code': '74000000-0000-4000-8000-000000000001.'+'a'*64, 'allow_updates': True}))
        self.fake = Fake()
        self.now = 1000000
        self.env = patch.dict(os.environ, {'SUPERVISOR_TOKEN': 'synthetic-only'})
        self.env.start()
        self.c = self.new()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def new(self):
        return m.Companion(self.root/'data', self.fake, lambda: self.now, self.options)

    def posts(self, suffix):
        return [p for url, p in self.fake.calls if url.endswith(suffix) and p is not None]

    def backup_ready(self):
        self.c.step()
        self.c.step()
        self.assertEqual(self.c.data['records'][ID]['phase'], 'backup_done')

    def test_update_waits_for_verified_encrypted_connector_only_backup(self):
        self.backup_ready()
        self.assertFalse(self.posts('/update'))
        payload = self.posts('/backups/new/partial')[0]
        self.assertEqual(payload['addons'], ['a123bc45_homekind_bridge'])
        self.assertEqual(payload['folders'], [])
        self.assertFalse(payload['homeassistant'])
        self.assertEqual(len(payload['password']), 64)
        self.c.step()
        self.assertEqual(len(self.posts('/update')), 1)
        self.c.step()
        self.assertEqual(self.c.data['records'][ID]['phase'], 'verify')
        self.c.step()
        self.now += 61
        self.c.step()
        self.assertEqual(self.c.data['records'][ID]['phase'], 'healthy')
        self.assertFalse(any('/options' in url for url, _ in self.fake.calls))

    def test_ambiguous_update_is_not_replayed_after_restart(self):
        self.backup_ready()
        self.fake.fail_update = True
        self.c.step()
        self.assertEqual(self.c.data['records'][ID]['phase'], 'unknown')
        self.c = self.new()
        for _ in range(3):
            self.c.step()
        self.assertEqual(len(self.posts('/update')), 1)

    def test_local_opt_out_stops_a_not_yet_dispatched_update(self):
        self.backup_ready()
        options = json.loads(self.options.read_text())
        options['allow_updates'] = False
        self.options.write_text(json.dumps(options))
        self.c.step()
        self.assertFalse(self.posts('/update'))

    def test_changed_repository_latest_fails_closed(self):
        self.backup_ready()
        self.fake.info['version_latest'] = '0.18.0'
        self.c.step()
        self.assertFalse(self.posts('/update'))

    def test_automatic_updates_cannot_bypass_staging(self):
        self.fake.info['auto_update'] = True
        with self.assertRaises(m.MaintenanceError):
            self.c.step()
        self.assertFalse(self.posts('/backups/new/partial'))

    def test_foreign_or_full_backup_is_never_restored(self):
        self.c.step()
        self.fake.bad_backup = True
        self.c.step()
        self.assertEqual(self.c.data['records'][ID]['phase'], 'failed')
        self.assertFalse(self.posts('/update'))

    def test_lost_authority_between_backup_and_update_stops_dispatch(self):
        self.backup_ready()
        self.fake.authorised = False
        self.c.step()
        self.assertFalse(self.posts('/update'))

    def test_recovery_is_exact_local_pre_update_backup_and_can_recover_stopped_connector(self):
        self.backup_ready()
        self.c.step()
        self.c.step()
        self.c.data['records'][ID]['phase'] = 'unknown'
        self.c.save()
        self.fake.info['state'] = 'stopped'
        self.fake.command = {'id': '74000000-0000-4000-8000-000000000021', 'operation': 'recover', 'before_version': '0.17.0', 'target_version': '0.16.1', 'backup_id': 'abcd1234', 'recovery_of': ID}
        self.c.step()
        self.c.step()
        restore = self.posts('/restore/partial')[0]
        self.assertEqual(restore['addons'], ['a123bc45_homekind_bridge'])
        self.assertFalse(restore['homeassistant'])
        self.assertEqual(restore['password'], self.c.data['records'][ID]['password'])

    def test_cloud_cannot_inject_target_url_or_backup(self):
        self.c.enrol(self.c.options())
        self.c.discover()
        for extra in [{'url': 'https://untrusted.invalid'}, {'slug': 'core_mosquitto'}]:
            with self.assertRaises(m.MaintenanceError):
                self.c.accept({**self.fake.command, **extra}, self.fake.info)

    def test_null_local_backup_alias_is_accepted(self):
        self.c.step()
        original = self.fake.__call__
        def call(url, token, payload=None, timeout=25):
            value = original(url, token, payload, timeout)
            if url.endswith('/backups/abcd1234/info'):
                value['data']['locations'] = [None]
                value['data']['location'] = None
            return value
        self.c.call = call
        self.c.step()
        self.assertEqual(self.c.data['records'][ID]['phase'], 'backup_done')

    def test_local_opt_out_during_commit_prevents_destructive_post(self):
        self.backup_ready()
        original = self.fake.__call__
        def call(url, token, payload=None, timeout=25):
            value = original(url, token, payload, timeout)
            if url == m.CLOUD+'/maintenance' and payload['operation'] == 'commit':
                options = json.loads(self.options.read_text())
                options['allow_updates'] = False
                self.options.write_text(json.dumps(options))
            return value
        self.c.call = call
        self.c.step()
        self.assertFalse(self.posts('/update'))

    def test_nested_job_failure_is_not_success(self):
        with self.assertRaises(m.MaintenanceError):
            m.job_finished({'done': True, 'errors': [], 'child_jobs': [{'done': True, 'errors': ['failed'], 'child_jobs': []}]})
        self.assertFalse(m.job_finished({'done': None, 'errors': [], 'child_jobs': []}))

    def test_journal_has_private_permissions_and_pairing_survives_restart(self):
        self.c.step()
        self.assertEqual(self.c.path.stat().st_mode & 0o777, 0o600)
        old = self.c.data['credential']
        restarted = self.new()
        restarted.enrol(restarted.options())
        self.assertEqual(restarted.data['credential'], old)


if __name__ == '__main__':
    unittest.main()
