import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homekind_maintenance.windows import WindowJournal, WindowError
from homekind_maintenance.window_sync import reconcile, report


class WindowSyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.journal = WindowJournal(Path(self.directory.name) / 'window.json')
        self.snapshot = {'policy': {'schema': 1, 'revision': 4, 'enabled': True,
                                   'zone': 'Australia/Melbourne', 'days': [0, 6],
                                   'start': '23:00', 'end': '03:00'},
                         'paused': False, 'defer_until': None}

    def tearDown(self):
        self.directory.cleanup()

    def response(self, **changes):
        return dict(protocol=1, authority_current=True, executor_ready=False,
                    desired=self.snapshot, **changes)

    def apply(self):
        return reconcile(self.journal, self.response(), locally_enabled=True)

    def test_offline_skipped_revisions_reconcile_idempotently_and_report_exact_choices(self):
        self.assertTrue(self.apply())
        self.assertTrue(self.apply())
        value = report(self.journal, management_id='b' * 32, locally_enabled=True,
                       inventory=[], now=datetime(2026, 10, 18, 5, tzinfo=timezone.utc))
        self.assertEqual(value['applied_revision'], 4)
        self.assertEqual(value['applied'], self.snapshot)
        self.assertFalse(value['executor_ready'])
        self.assertEqual(value['next_window']['revision'], 4)

    def test_opt_out_and_ended_authority_preserve_local_file_and_do_not_acknowledge(self):
        self.apply()
        before = self.journal.path.read_bytes()
        self.snapshot['policy']['revision'] = 5
        self.assertFalse(reconcile(self.journal, self.response(), locally_enabled=False))
        ended = self.response()
        ended['authority_current'] = False
        self.assertFalse(reconcile(self.journal, ended, locally_enabled=True))
        self.assertEqual(before, self.journal.path.read_bytes())

    def test_stale_or_changed_same_revision_never_overwrites_local_choices(self):
        self.apply()
        for revision in [3, 4]:
            self.snapshot['policy']['revision'] = revision
            self.snapshot['policy']['start'] = '22:00'
            with self.assertRaises(WindowError):
                self.apply()
        self.assertEqual(self.journal.read()['policy']['start'], '23:00')

    def test_new_policy_preserves_an_unknown_physical_operation(self):
        self.apply()
        with self.journal.locked():
            data = self.journal.read()
            data['inflight'] = {'operation_id': '89000000-0000-4000-8000-000000000001',
                                'phase': 'unknown', 'occurrence': 'previous'}
            from homekind_maintenance.maintenance import atomic
            atomic(self.journal.path, data)
        self.snapshot['policy']['revision'] = 6
        self.snapshot['paused'] = True
        self.apply()
        self.assertEqual(self.journal.read()['inflight']['phase'], 'unknown')
        value = report(self.journal, management_id='b' * 32, locally_enabled=True,
                       inventory=[], now=datetime.now(timezone.utc))
        self.assertIsNone(value['next_window'])

    def test_unknown_protocol_and_execution_fields_fail_closed_without_rewriting(self):
        self.apply()
        before = self.journal.path.read_bytes()
        for change in [{'protocol': True}, {'protocol': 2}, {'executor_ready': 'true'}, {'command': 'update.install'}]:
            value = self.response()
            value.update(change)
            with self.assertRaises(WindowError):
                reconcile(self.journal, value, locally_enabled=True)
        self.assertEqual(before, self.journal.path.read_bytes())

    def test_report_rejects_arbitrary_fields_and_credentials_before_any_transport(self):
        self.apply()
        item = dict(id='core', kind='core', name='Home Assistant Core', installed='2026.10.2',
                    available=None, can_install=True, vendor_managed=False)
        for inventory in [[dict(item, token='secret')], [dict(item, installed='https://wrong.invalid')],
                          [item, item], [dict(item, can_install='true')]]:
            with self.assertRaises(WindowError):
                report(self.journal, management_id='b' * 32, locally_enabled=True,
                       inventory=inventory, now=datetime.now(timezone.utc))


if __name__ == '__main__':
    unittest.main()
