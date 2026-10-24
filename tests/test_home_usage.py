import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'homekind_bridge'))
from control_transport import UsageMeter, CommandWake
from light_group_adapter import LightGroupWorker

BRIDGE = '00000000-0000-4000-8000-000000000001'
URL = 'https://saixlksmjzbakopvbsqv.supabase.co/functions/v1/homekind-home-bridge/collect'


class UsageTests(unittest.TestCase):
    def test_only_cumulative_numbers_and_route_names_leave_the_meter(self):
        meter = UsageMeter()
        data = {'bridge_id': BRIDGE, 'token': 'must-not-leave', 'states': [{'private': 'reading'}]}
        meter.record(URL+'?private=must-not-leave', data, 120, 200, 35.4, False)
        meter.record(URL, data, 100, 20, 8, True)
        snapshot = meter.snapshot(BRIDGE)
        row = snapshot['routes']['homekind-home-bridge/collect']
        self.assertEqual(row, dict(requests=2, errors=1, request_bytes=220, response_bytes=220, duration_ms=43))
        self.assertNotIn('must-not-leave', str(snapshot))
        self.assertNotIn('private', str(snapshot))
        row['requests'] = 500
        self.assertEqual(meter.snapshot(BRIDGE)['routes']['homekind-home-bridge/collect']['requests'], 2)

    def test_local_requests_unpaired_calls_and_other_hosts_are_not_counted(self):
        meter = UsageMeter()
        for url, data in [('http://supervisor/core/api/states', {'bridge_id': BRIDGE}),
                          (URL.replace('saixlksmjzbakopvbsqv', 'other'), {'bridge_id': BRIDGE}), (URL, {})]:
            meter.record(url, data, 1, 1, 1, False)
        self.assertEqual(meter.snapshot(BRIDGE)['routes'], {})

    def test_healthy_push_channel_uses_no_idle_poll_until_recovery_deadline(self):
        wake = CommandWake(SimpleNamespace(), connect=lambda: None)
        event = SimpleNamespace(wait=lambda timeout: self.assertEqual(timeout, 30), clear=lambda: None)
        wake.events['device'] = event
        wake.connected.set()
        wake.last_heartbeat = time.monotonic()
        wake.wait('device')

    def test_stale_or_disconnected_channel_restores_half_second_polling(self):
        wake = CommandWake(SimpleNamespace(), connect=lambda: None)
        event = SimpleNamespace(wait=lambda timeout: self.assertEqual(timeout, .5), clear=lambda: None)
        wake.events['device'] = event
        wake.wait('device')
        wake.connected.set()
        wake.last_heartbeat = time.monotonic()-7
        wake.wait('device')

    def test_queued_command_wakes_without_waiting_for_the_idle_deadline(self):
        wake = CommandWake(SimpleNamespace(), connect=lambda: None)
        wake.connected.set()
        wake.last_heartbeat = time.monotonic()
        finished = threading.Event()
        thread = threading.Thread(target=lambda: (wake.wait('device'), finished.set()))
        thread.start()
        wake.signal('device')
        self.assertTrue(finished.wait(.2))
        thread.join(1)

    def test_group_capability_is_renewed_before_the_server_lease_expires(self):
        clock = [0]
        advertisements = []
        ready = threading.Event()
        ready.set()
        def wait(_):
            clock[0] += 31
        bridge = SimpleNamespace(ready=ready, stopping=lambda:clock[0]>=93,
                                 clock=lambda:clock[0], wait_commands=wait,
                                 command_wake=SimpleNamespace(healthy=lambda:True))
        worker = LightGroupWorker(bridge)
        def advertise():
            advertisements.append(clock[0])
            worker.adapter.generation = 1
            return True
        worker.adapter.advertise = advertise
        worker.adapter.run_once = lambda:None
        worker.run()
        self.assertEqual(advertisements, [0,31,62])


if __name__ == '__main__':
    unittest.main()
