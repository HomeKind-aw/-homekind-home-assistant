import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homekind_maintenance.windows import WindowError, WindowJournal, policy, occurrences, evaluate


def at(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def choice(**changes):
    return {'schema': 1, 'revision': 1, 'enabled': True, 'zone': 'Australia/Melbourne',
            'days': list(range(7)), 'start': '23:00', 'end': '04:00', **changes}


CONTEXT = dict(estimate_seconds=600, locally_enabled=True, authority_current=True,
               service_ready=True, active_use=False)
A = '90000000-0000-4000-8000-000000000001'
B = '90000000-0000-4000-8000-000000000002'


class Windows(unittest.TestCase):
    def test_overnight_uses_start_day_and_end_is_exclusive(self):
        p = choice(zone='UTC', days=[0])
        yes = evaluate(p, at('2026-10-20T01:00:00Z'), **CONTEXT)
        self.assertTrue(yes['eligible'])
        self.assertEqual(yes['occurrence']['start'], at('2026-10-19T23:00:00Z'))
        self.assertFalse(evaluate(p, at('2026-10-20T04:00:00Z'), **CONTEXT)['eligible'])

    def test_missed_window_offline_and_restart_never_catch_up_in_daytime(self):
        p = choice(zone='UTC', days=[0])
        verdict = evaluate(p, at('2026-10-20T11:00:00Z'), **CONTEXT)
        self.assertEqual(verdict['reason'], 'outside_window')
        self.assertEqual(verdict['next']['start'], at('2026-10-26T23:00:00Z'))
        self.assertIsNone(verdict['occurrence'])

    def test_melbourne_spring_missing_boundary_skips_to_next_week(self):
        p = choice(days=[6], start='02:30', end='04:00')
        rows = occurrences(p, at('2026-10-03T15:45:00Z'))
        self.assertEqual(rows[0]['start'], at('2026-10-10T15:30:00Z'))
        self.assertEqual(evaluate(p, at('2026-10-03T16:30:00Z'), **CONTEXT)['reason'], 'outside_window')

    def test_spring_overnight_remaining_time_uses_real_utc_duration(self):
        p = choice(days=[5])
        now = at('2026-10-03T16:20:00Z')
        result = evaluate(p, now, **{**CONTEXT, 'estimate_seconds': 1800})
        self.assertTrue(result['eligible'])
        self.assertEqual((result['occurrence']['end'] - result['occurrence']['start']).total_seconds(), 4 * 3600)
        self.assertEqual(evaluate(p, now, **{**CONTEXT, 'estimate_seconds': 2400})['reason'], 'insufficient_time')

    def test_fall_repeated_start_uses_later_fold_and_end_earlier_fold(self):
        p = choice(days=[6], start='02:15', end='03:30')
        self.assertEqual(evaluate(p, at('2026-04-04T15:30:00Z'), **CONTEXT)['reason'], 'outside_window')
        later = evaluate(p, at('2026-04-04T16:30:00Z'), **CONTEXT)
        self.assertTrue(later['eligible'])
        self.assertEqual(later['occurrence']['start'], at('2026-04-04T16:15:00Z'))
        q = choice(days=[6], start='01:30', end='02:45')
        self.assertEqual(occurrences(q, at('2026-04-04T14:30:00Z'))[0]['end'], at('2026-04-04T15:45:00Z'))

    def test_both_ambiguous_boundaries_that_reverse_are_skipped(self):
        p = choice(days=[6], start='02:15', end='02:45')
        rows = occurrences(p, at('2026-04-04T15:00:00Z'))
        self.assertEqual(rows[0]['start'].astimezone(timezone.utc).date().isoformat(), '2026-04-11')

    def test_non_hour_dst_transition_and_quarter_hour_timezone(self):
        p = choice(zone='Australia/Lord_Howe', days=[6], start='02:15', end='04:00')
        rows = occurrences(p, at('2026-10-03T14:00:00Z'))
        self.assertEqual(rows[0]['start'].date().isoformat(), '2026-10-10')
        p = choice(zone='Asia/Kathmandu', start='00:15', end='01:15')
        self.assertTrue(evaluate(p, at('2026-10-18T18:45:00Z'), **CONTEXT)['eligible'])

    def test_opt_out_pause_deferral_permission_and_unknown_use_are_denials(self):
        p = choice(zone='UTC')
        now = at('2026-10-19T01:00:00Z')
        cases = [('locally_enabled', False, 'local_opt_out'), ('authority_current', False, 'permission_unverified'),
                 ('service_ready', False, 'home_not_ready'), ('active_use', True, 'home_not_ready'),
                 ('active_use', None, 'home_not_ready')]
        for key, value, reason in cases:
            self.assertEqual(evaluate(p, now, **{**CONTEXT, key: value})['reason'], reason)
        self.assertEqual(evaluate(p, now, **CONTEXT, paused=True)['reason'], 'paused')
        self.assertEqual(evaluate(p, now, **CONTEXT, defer_until=at('2026-10-19T04:00:00Z'))['reason'], 'deferred')
        self.assertEqual(evaluate(choice(zone='UTC', enabled=False), now, **CONTEXT)['reason'], 'local_opt_out')

    def test_typed_policy_and_clock_fail_closed(self):
        for changes in [dict(days=[True]), dict(days=[0, 0]), dict(days=[]), dict(zone='../UTC'), dict(zone='No/Such_Zone'),
                        dict(start='24:00'), dict(start='03:00', end='03:00'), dict(start='03:00', end='17:00'),
                        dict(revision=True), dict(schema=True), dict(command='restart')]:
            with self.assertRaises(WindowError):
                policy(choice(**changes))
        with self.assertRaises(WindowError):
            evaluate(choice(), datetime(2026, 1, 1), **CONTEXT)
        for estimate in [0, -1, True, 90000]:
            with self.assertRaises(WindowError):
                evaluate(choice(), at('2026-10-18T16:00:00Z'), **{**CONTEXT, 'estimate_seconds': estimate})

    def test_private_journal_reservation_survives_restart_and_no_replay(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'window.json'
            first = WindowJournal(path)
            first.configure(choice(zone='UTC'), 0)
            now = at('2026-10-19T01:00:00Z')
            self.assertTrue(first.reserve(A, now, expected_revision=1, **CONTEXT)['eligible'])
            second = WindowJournal(path)
            self.assertEqual(second.reserve(B, now, expected_revision=1, **CONTEXT)['reason'], 'update_in_progress')
            second.finish(A, verified=False)
            self.assertEqual(WindowJournal(path).reserve(B, now, expected_revision=1, **CONTEXT)['reason'], 'previous_outcome_unknown')
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_verified_receipt_and_pause_revision_do_not_reopen_same_occurrence(self):
        with tempfile.TemporaryDirectory() as d:
            j = WindowJournal(Path(d) / 'window.json')
            j.configure(choice(zone='UTC'), 0)
            now = at('2026-10-19T01:00:00Z')
            j.reserve(A, now, expected_revision=1, **CONTEXT)
            j.finish(A, verified=True)
            j.hold(expected_revision=1, paused=True)
            j.hold(expected_revision=2, paused=False)
            self.assertEqual(j.reserve(B, now, expected_revision=3, **CONTEXT)['reason'], 'already_completed')
            self.assertEqual(j.reserve(B, at('2026-10-18T23:30:00Z'), expected_revision=3, **CONTEXT)['reason'], 'clock_rollback')

    def test_unknown_and_corrupt_journals_are_preserved_not_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'window.json'
            j = WindowJournal(path)
            for content in ['{"schema":99}', '{broken', 'x' * 65537]:
                path.write_text(content)
                with self.assertRaises(WindowError):
                    j.configure(choice(zone='UTC'), 0)
                self.assertEqual(path.read_text(), content)

    def test_stale_policy_revision_and_pause_persist(self):
        with tempfile.TemporaryDirectory() as d:
            j = WindowJournal(Path(d) / 'window.json')
            j.configure(choice(zone='UTC'), 0)
            with self.assertRaises(WindowError):
                j.configure(choice(zone='UTC', revision=2), 0)
            with self.assertRaises(WindowError):
                j.hold(expected_revision=True, paused=True)
            j.hold(expected_revision=1, paused=True, defer_until=at('2026-10-20T05:00:00Z'))
            loaded = WindowJournal(j.path).read()
            self.assertTrue(loaded['paused'])
            self.assertEqual(loaded['policy']['revision'], 2)
            self.assertEqual(loaded['defer_until'], '2026-10-20T05:00:00+00:00')

    def test_two_independent_candidates_serialise_one_reservation(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'window.json'
            WindowJournal(path).configure(choice(zone='UTC'), 0)
            barrier = threading.Barrier(2)
            results = []
            def reserve(operation):
                barrier.wait()
                results.append(WindowJournal(path).reserve(operation, at('2026-10-19T01:00:00Z'), expected_revision=1, **CONTEXT))
            threads = [threading.Thread(target=reserve, args=(i,)) for i in (A, B)]
            for t in threads: t.start()
            for t in threads: t.join(timeout=5)
            self.assertTrue(all(not t.is_alive() for t in threads))
            self.assertEqual(len(results), 2)
            self.assertEqual(sum(r['eligible'] for r in results), 1)


if __name__ == '__main__':
    unittest.main()
