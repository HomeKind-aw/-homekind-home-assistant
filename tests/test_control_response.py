import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).parents[1] / 'homekind_bridge'
sys.path.insert(0, str(ROOT))
from control_transport import JsonConnections, HACommands, TransportError, NotConnected
spec = importlib.util.spec_from_file_location('response_bridge', ROOT / 'bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class ControlResponseTests(unittest.TestCase):
    def test_http_connections_are_reused_and_redirects_are_not_followed(self):
        sockets = []
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def do_POST(self):
                sockets.append(self.connection)
                self.rfile.read(int(self.headers['Content-Length']))
                data = b'{}'
                self.send_response(302 if self.path == '/redirect' else 200)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Location', '/must-not-follow')
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            client = JsonConnections()
            url = 'http://127.0.0.1:' + str(server.server_port)
            for _ in range(2):
                self.assertEqual(client.request(url, 'synthetic', {}), {})
            self.assertIs(sockets[0], sockets[1])
            with self.assertRaises(TransportError):
                client.request(url + '/redirect', 'synthetic', {})
            self.assertEqual(len(sockets), 3)
        finally:
            server.shutdown()
            server.server_close()

    def test_lost_http_post_response_is_not_replayed(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                calls.append(1)
                self.connection.close()
                self.close_connection = True
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with self.assertRaises(TransportError):
                JsonConnections().request('http://127.0.0.1:' + str(server.server_port), 'synthetic', {}, timeout=1)
            self.assertEqual(len(calls), 1)
        finally:
            server.shutdown()
            server.server_close()

    def test_next_command_uses_a_free_slot_while_an_earlier_device_is_slow(self):
        entered, later, release = threading.Event(), threading.Event(), threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            c = bridge.Connector({'read_only': False}, directory, request=lambda *args: {})
            c.ready.set()
            pending = [{'id': 'slow'}, {'id': 'fast'}, {'id': 'later'}]
            lock = threading.Lock()
            def collect(lane, limit):
                with lock:
                    result = pending[:limit]
                    del pending[:limit]
                    return result
            c.collect = collect
            def execute(claim):
                if claim['id'] == 'slow':
                    entered.set()
                    release.wait(3)
                elif claim['id'] == 'later':
                    later.set()
            c.execute = execute
            # The first poll returns two, leaving capacity for a subsequent poll.
            first = True
            def limited(lane, limit):
                nonlocal first
                result = collect(lane, min(limit, 2) if first else limit)
                first = False
                return result
            c.collect = limited
            worker = threading.Thread(target=c.command_loop, args=(lambda _: None,))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(later.wait(1), 'The next poll waited for the slow batch')
                self.assertFalse(release.is_set())
            finally:
                c.halt.set()
                release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())

    def test_uncertain_websocket_send_never_falls_back_to_http(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            c = bridge.Connector({}, directory, request=lambda *args, **kwargs: calls.append(args) or {})
            c.native_transport = True
            class Socket:
                def call(self, *args, **kwargs):
                    raise TransportError('uncertain')
            c.control_socket = Socket()
            with self.assertRaises(bridge.BridgeError):
                c.ha('services/light/turn_on', {'entity_id': 'light.fixture'})
            self.assertEqual(calls, [])
            class Offline:
                def call(self, *args, **kwargs):
                    raise NotConnected('not sent')
            c.control_socket = Offline()
            c.ha('services/light/turn_on', {'entity_id': 'light.fixture'})
            self.assertEqual(len(calls), 1)

    def test_websocket_matches_results_and_sends_one_action(self):
        class C:
            pass
        commands = HACommands(C(), connect=lambda: None)
        sent = []
        class Socket:
            def send(self, text):
                value = json.loads(text)
                sent.append(value)
                record = commands.pending[value['id']]
                record['result'] = {'success': True, 'result': {'context': {'id': 'fixture'}}}
                record['done'].set()
        commands.socket = Socket()
        commands.connected.set()
        self.assertEqual(commands.call('light', 'turn_on', {'entity_id': 'light.fixture'})['context']['id'], 'fixture')
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['type'], 'call_service')
        self.assertEqual(commands.pending, {})

    def test_live_cache_is_not_used_when_the_ha_stream_is_disconnected(self):
        with tempfile.TemporaryDirectory() as directory:
            c = bridge.Connector({}, directory, request=lambda *args, **kwargs: {'state': 'fresh'})
            c.native_transport = True
            c.current['light.fixture'] = {'state': 'old'}
            c.stream_connected = True
            self.assertEqual(c.cached_states(['light.fixture'])['light.fixture']['state'], 'old')
            c.stream_connected = False
            self.assertIsNone(c.cached_states(['light.fixture']))
            self.assertEqual(c.ha('states/light.fixture')['state'], 'fresh')


if __name__ == '__main__':
    unittest.main()
