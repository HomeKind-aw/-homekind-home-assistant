"""Persistent transports. No credentials, entity names or payloads are logged."""
import http.client
import json
import ssl
import threading
import time
import urllib.parse


class TransportError(Exception):
    pass


class TransportDenied(TransportError):
    pass


class NotConnected(TransportError):
    """Raised only before an action has been sent."""


class JsonConnections:
    """One connection per origin per worker; uncertain POSTs are never retried."""
    def __init__(self):
        self.local = threading.local()
        self.context = ssl.create_default_context()

    def request(self, url, token, data=None, timeout=12):
        target = urllib.parse.urlsplit(url)
        if target.scheme not in ('http', 'https') or not target.hostname or target.username or target.password or target.fragment:
            raise TransportError('Invalid connection destination.')
        connections = getattr(self.local, 'connections', None)
        if connections is None:
            connections = self.local.connections = {}
        key = (target.scheme, target.hostname, target.port)
        connection = connections.get(key)
        if connection is None:
            connection = (http.client.HTTPSConnection(target.hostname, target.port, timeout=timeout, context=self.context)
                          if target.scheme == 'https' else http.client.HTTPConnection(target.hostname, target.port, timeout=timeout))
            connections[key] = connection
        connection.timeout = timeout
        if connection.sock:
            connection.sock.settimeout(timeout)
        payload = None if data is None else json.dumps(data, separators=(',', ':'), allow_nan=False).encode()
        path = urllib.parse.urlunsplit(('', '', target.path or '/', target.query, ''))
        try:
            connection.request('GET' if data is None else 'POST', path, body=payload,
                               headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'Connection': 'keep-alive'})
            response = connection.getresponse()
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise TransportError('Response is too large.')
            if response.status in (401, 403):
                raise TransportDenied('Connection access ended.')
            if not 200 <= response.status < 300:
                raise TransportError('HTTP ' + str(response.status))
            return json.loads(raw)
        except (OSError, http.client.HTTPException, ValueError, TransportError) as error:
            connection.close()
            connections.pop(key, None)
            if isinstance(error, TransportError):
                raise
            raise TransportError('Connection unavailable.') from None


class HACommands(threading.Thread):
    """Separate HA action socket; the sensor socket remains read-only."""
    def __init__(self, connector, connect=None):
        super().__init__(name='homekind-ha-controls', daemon=True)
        if connect is None:
            from websockets.sync.client import connect
        self.connect = connect
        self.c = connector
        self.lock = threading.Lock()
        self.socket = None
        self.pending = {}
        self.sequence = 0
        self.connected = threading.Event()

    def call(self, domain, service, parameters, timeout=3):
        done = threading.Event()
        with self.lock:
            if not self.connected.is_set() or self.socket is None:
                raise NotConnected('Home Assistant controls are reconnecting.')
            self.sequence += 1
            ident = self.sequence
            record = {'done': done, 'result': None}
            self.pending[ident] = record
            try:
                # Once send begins, the caller must never switch transports/replay.
                self.socket.send(json.dumps({'id': ident, 'type': 'call_service', 'domain': domain,
                                             'service': service, 'service_data': parameters}))
            except Exception:
                self.pending.pop(ident, None)
                raise TransportError('Action outcome is uncertain.') from None
        try:
            if not done.wait(timeout):
                raise TransportError('Action outcome is uncertain.')
            result = record['result']
            if not isinstance(result, dict) or result.get('success') is not True:
                raise TransportError('Home Assistant did not confirm the action request.')
            return result.get('result') or {}
        finally:
            with self.lock:
                self.pending.pop(ident, None)

    def stop(self):
        self.connected.clear()
        socket = self.socket
        if socket:
            socket.close()

    def run(self):
        delay = .5
        while not self.c.stopping():
            try:
                url = ('ws://supervisor/core/websocket' if self.c.supervisor else
                       self.c.ha_url.replace('http://', 'ws://', 1).replace('https://', 'wss://', 1) + '/websocket')
                with self.connect(url, proxy=None, compression=None, open_timeout=3, close_timeout=1,
                                  ping_interval=10, ping_timeout=5, max_size=1_000_000) as socket:
                    if json.loads(socket.recv(timeout=3)).get('type') != 'auth_required':
                        raise TransportError('Invalid Home Assistant handshake.')
                    socket.send(json.dumps({'type': 'auth', 'access_token': self.c.ha_token}))
                    if json.loads(socket.recv(timeout=3)).get('type') != 'auth_ok':
                        raise TransportDenied('Home Assistant access ended.')
                    with self.lock:
                        self.socket = socket
                        self.connected.set()
                    delay = .5
                    while not self.c.stopping():
                        try:
                            result = json.loads(socket.recv(timeout=.5))
                        except TimeoutError:
                            continue
                        if isinstance(result, dict) and result.get('type') == 'result':
                            with self.lock:
                                record = self.pending.get(result.get('id'))
                                if record:
                                    record['result'] = result
                                    record['done'].set()
            except Exception:
                pass  # HTTP can be selected before a send; never replay after a send.
            finally:
                with self.lock:
                    self.connected.clear()
                    self.socket = None
                    for record in self.pending.values():
                        record['done'].set()
            self.c.halt.wait(delay)
            delay = min(delay * 2, 10)


class CommandWake(threading.Thread):
    """Authenticated invalidations only. Commands still use durable leases."""
    def __init__(self, connector, connect=None):
        super().__init__(name='homekind-command-wake', daemon=True)
        if connect is None:
            from websockets.sync.client import connect
        self.connect = connect
        self.c = connector
        self.connected = threading.Event()
        self.socket = None
        self.events = {lane: threading.Event() for lane in ('device', 'media', 'group')}

    def signal(self, lane=None):
        for key, event in self.events.items():
            if lane is None or lane == key:
                event.set()

    def stop(self):
        self.connected.clear()
        self.signal()
        if self.socket:
            self.socket.close()

    def wait(self, lane, timeout=None):
        event = self.events[lane]
        event.wait((2 if self.connected.is_set() else .5) if timeout is None else timeout)
        event.clear()

    def run(self):
        delay = 1
        while not self.c.stopping():
            try:
                bridge = self.c.credentials.get('bridge_id', '')
                # No bearer credential in a URL, subprotocol, log or stored file.
                url = self.c.cloud_root.replace('https://', 'wss://', 1).rsplit('/', 1)[0] + '/homekind-command-wake?bridge_id=' + urllib.parse.quote(bridge, safe='')
                with self.connect(url, additional_headers={'Authorization': 'Bearer ' + self.c.credentials['token']},
                                  proxy=None, compression=None, open_timeout=5, close_timeout=1,
                                  ping_interval=10, ping_timeout=5, max_size=2048) as socket:
                    self.socket = socket
                    while not self.c.stopping():
                        try:
                            message = json.loads(socket.recv(timeout=1))
                        except TimeoutError:
                            continue
                        if not isinstance(message, dict):
                            break
                        if message == {'type': 'ready'}:
                            self.connected.set()
                            delay = 1
                            self.signal()  # Recover anything committed before subscription.
                        elif message.get('type') == 'wake' and message.get('lane') in self.events and set(message) == {'type', 'lane'}:
                            self.signal(message['lane'])
                        elif message.get('type') == 'heartbeat':
                            continue
                        else:
                            break
            except Exception:
                pass  # Optional transport failures leave the regular poller running.
            finally:
                self.connected.clear()
                self.socket = None
                self.signal()
            self.c.halt.wait(delay)
            delay = min(delay * 2, 30)
