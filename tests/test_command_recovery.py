"""Pre-dispatch recovery must never replay an uncertain physical action."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).parents[1] / 'homekind_bridge'
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('recovery_bridge', ROOT / 'bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class CommandRecoveryTests(unittest.TestCase):
    def run_failed_authorisation(self, errors, *, expire=False, stop=False):
        now = [100]
        calls, timings = [], []
        with tempfile.TemporaryDirectory() as directory:
            c = bridge.Connector({'read_only': False}, directory, request=lambda *a: {}, clock=lambda: now[0])
            c.ha = lambda *a: {'entity_id': 'light.fixture', 'state': 'off', 'attributes': {}}
            c.timing = lambda lane, claim, values: timings.append(values.copy())
            def cloud(operation, data=None):
                calls.append(operation)
                if operation == 'authorize':
                    if expire:
                        now[0] = 201
                    if stop:
                        c.halt.set()
                    raise errors[min(calls.count('authorize') - 1, len(errors) - 1)]
                return {}
            c.cloud = cloud
            claim = {'id': 'synthetic', 'lease_id': 'synthetic', 'entity_id': 'light.fixture',
                     'expires_at': '1970-01-01T00:03:20Z'}
            try:
                c.execute(claim)
            except bridge.AccessEnded:
                pass
        return calls, timings

    def test_transport_recovery_is_bounded_and_keeps_failure_timings(self):
        calls, timings = self.run_failed_authorisation([bridge.BridgeError('Connection unavailable.')])
        self.assertEqual(calls, ['authorize', 'authorize'])
        self.assertEqual(len(timings), 1)
        self.assertIn('authorize_ms', timings[0])
        self.assertIn('connector_total_ms', timings[0])

    def test_a_server_that_already_authorised_does_not_allow_an_action_replay(self):
        calls, timings = self.run_failed_authorisation([
            bridge.BridgeError('Connection unavailable.'), bridge.BridgeError('HTTP 409')])
        self.assertEqual(calls, ['authorize', 'authorize'])
        self.assertEqual(len(timings), 1)

    def test_permission_denial_is_not_retried(self):
        calls, _ = self.run_failed_authorisation([bridge.AccessEnded('Access ended.')])
        self.assertEqual(calls, ['authorize'])

    def test_an_expired_original_deadline_is_not_extended_for_recovery(self):
        calls, _ = self.run_failed_authorisation([bridge.BridgeError('Connection unavailable.')], expire=True)
        self.assertEqual(calls, ['authorize'])

    def test_stopping_the_connector_aborts_recovery(self):
        calls, _ = self.run_failed_authorisation([bridge.BridgeError('Connection unavailable.')], stop=True)
        self.assertEqual(calls, ['authorize'])

    def test_stale_lease_and_other_errors_are_not_retried(self):
        for message in ['HTTP 409', 'HTTP 403', 'HTTP 429', 'HTTP 400']:
            with self.subTest(message=message):
                calls, _ = self.run_failed_authorisation([bridge.BridgeError(message)])
                self.assertEqual(calls, ['authorize'])


if __name__ == '__main__':
    unittest.main()
