import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homekind_maintenance.window_runtime import WindowRuntime
from homekind_maintenance.windows import WindowError


class WindowRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.enabled = True; self.sent = []; self.change = None
        self.snapshot = {'policy': {'schema': 1, 'revision': 3, 'enabled': True,
            'zone': 'Australia/Melbourne', 'days': [0, 1, 2, 3, 4, 5, 6],
            'start': '02:00', 'end': '05:00'}, 'paused': False, 'defer_until': None}
        self.c = SimpleNamespace(directory=Path(self.temp.name), target='a123bc45_homekind_bridge',
            data={'credential': {'id': '74000000-0000-4000-8000-000000000001', 'token': 'a' * 64}},
            options=lambda: {'allow_updates': self.enabled}, clock=lambda: datetime(2026, 10, 18, tzinfo=timezone.utc).timestamp(),
            supervisor=self.supervisor, call=self.call)
        self.runtime = WindowRuntime(self.c)

    def supervisor(self, path, timeout):
        self.assertEqual(timeout, 2)
        return {'version': '2026.10.1', 'version_latest': '2026.10.2', 'secret': 'never share'}

    def call(self, url, token, body, timeout):
        self.assertTrue(url.endswith('/maintenance-windows')); self.assertEqual(timeout, 2)
        self.sent.append(copy.deepcopy(body))
        if self.change:
            self.change(); self.change = None
        return {'protocol': 1, 'authority_current': True, 'executor_ready': False, 'desired': self.snapshot}

    def test_save_precedes_exact_ack_and_restart_preserves_nonce(self):
        self.runtime.step()
        self.assertIsNone(self.sent[0]['report']['applied'])
        self.assertEqual(self.sent[1]['report']['applied'], self.snapshot)
        nonce = self.sent[0]['report']['management_id']
        WindowRuntime(self.c).step()
        self.assertEqual(self.sent[2]['report']['management_id'], nonce)
        self.assertNotIn('secret', json.dumps(self.sent))
        self.assertTrue(all(not r['can_install'] for r in self.sent[0]['report']['inventory']))

    def test_opt_out_and_reenable_rotate_nonce_and_do_not_ack_old_file(self):
        self.runtime.step(); nonce = self.sent[0]['report']['management_id']
        self.enabled = False; self.runtime.step()
        self.assertEqual(self.sent[-1]['report']['management_id'], '')
        self.assertIsNone(self.sent[-1]['report']['applied'])
        self.enabled = True; self.runtime.step()
        self.assertNotEqual(self.sent[-1]['report']['management_id'], nonce)
        self.assertIsNone(self.sent[-2]['report']['applied'])

    def test_pairing_change_isolates_journal(self):
        self.runtime.step(); old = self.runtime.journal.path
        self.c.data['credential']['token'] = 'b' * 64
        self.runtime.step()
        self.assertNotEqual(old, self.runtime.journal.path)
        self.assertIsNone(self.sent[-2]['report']['applied'])

    def test_local_opt_out_during_network_prevents_apply(self):
        self.change = lambda: setattr(self, 'enabled', False)
        with self.assertRaises(WindowError): self.runtime.step()
        self.assertIsNone(self.runtime.journal.read())
        self.assertEqual(len(self.sent), 1)

    def test_inventory_component_failure_is_isolated(self):
        original = self.c.supervisor
        def missing(path, timeout):
            if path == '/os/info': raise OSError('unavailable')
            return original(path, timeout)
        self.c.supervisor = missing
        self.runtime.step()
        self.assertNotIn('os', [r['id'] for r in self.sent[0]['report']['inventory']])

    def test_unknown_response_does_not_save(self):
        self.c.call = lambda *a, **k: {'protocol': 9}
        with self.assertRaises(WindowError): self.runtime.step()
        self.assertIsNone(self.runtime.journal.read())

    def test_corrupt_enrolment_is_preserved(self):
        self.runtime.path.write_text('{bad')
        with self.assertRaises(WindowError): self.runtime.step()
        self.assertEqual(self.runtime.path.read_text(), '{bad')
        self.assertEqual(self.sent, [])

    def test_real_executor_reports_preparation_before_any_backup_and_rechecks_authority(self):
        now=[datetime(2026,10,18,15,5,tzinfo=timezone.utc).timestamp()]
        self.c.clock=lambda:now[0];self.c.token='synthetic-local'
        self.c.options=lambda:{'allow_updates':True,'allow_system_updates':True}
        self.snapshot['policy'].update(days=list(range(7)),start='02:00',end='05:00')
        posts=[];extra={}
        def supervisor(path,payload=None,timeout=2):
            if payload is not None:
                posts.append(path)
                if path=='/backups/new/full':
                    extra.update(payload['extra']);return {'job_id':'b'*32}
                raise AssertionError('Unexpected operation '+path)
            if path=='/supervisor/info':return {'version':'2026.10.1','healthy':True,'supported':True,'channel':'stable'}
            return {'version':'2026.10.1','version_latest':'2026.10.2'}
        cloud=self.c.call
        def call(url,token,body,timeout):
            if url=='http://supervisor/core/api/template':return True
            response=cloud(url,token,body,timeout);response['executor_ready']=True;return response
        self.c.supervisor=supervisor;self.c.call=call
        self.runtime.step()
        self.assertEqual(posts,[])
        self.assertEqual(self.sent[-1]['report']['execution']['phase'],'preparing')
        self.assertTrue(self.sent[-1]['report']['executor_ready'])
        now[0]+=31;self.runtime.step()
        self.assertEqual(posts,['/backups/new/full'])
        self.assertEqual(self.sent[-1]['report']['execution']['phase'],'backup')
        self.assertEqual(self.sent[-2]['report']['execution']['phase'],'preparing')
        self.assertNotIn('password',json.dumps(self.sent))


if __name__ == '__main__': unittest.main()
