"""Opt-in, receive-only camera signalling. No video, RTSP URLs or credentials leave HA."""
import collections
import datetime as dt
import json
import logging
import queue
import re
import threading
import time
import uuid

CAMERA_URL = "https://saixlksmjzbakopvbsqv.supabase.co/functions/v1/homekind-camera-bridge/exchange"
ENTITY = re.compile(r"^camera\.[a-z0-9_]{1,150}$")
IDENTIFIER = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def receive_only_offer(value):
    if not isinstance(value, str) or not value.startswith("v=0") or len(value) > 65536:
        return False
    if re.search(r"(^|[\r\n])a=(sendrecv|sendonly)", value, re.I):
        return False
    parts = value.replace("\r\n", "\n").split("\nm=")[1:]
    if not 1 <= len(parts) <= 6:
        return False
    video = False
    for part in parts:
        if part.startswith(("audio ", "video ")):
            if not re.search(r"(^|[\r\n])a=(recvonly|inactive)([\r\n]|$)", part):
                return False
            video = video or part.startswith("video ")
        elif not part.startswith("application "):
            return False
    return video


class CameraSession(threading.Thread):
    def __init__(self, connector, session, emit, connect=None, clock=time.monotonic):
        super().__init__(name="homekind-camera-session", daemon=True)
        self.connector, self.session, self.emit, self.connect, self.clock = connector, session, emit, connect, clock
        self.stopped = threading.Event()
        self.messages = queue.Queue(maxsize=160)
        self.pending = []
        self.socket = None
        self.ha_session = None
        self.sequence = 0
        self.command_id = 3
        self.lease_until = clock() + 12
        self.deadline = clock() + 300
        self.failed = False

    def renew(self):
        self.lease_until = self.clock() + 12

    def stop(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        if self.socket:
            try:
                if self.ha_session:
                    self.socket.send(json.dumps({"id": 99999, "type": "unsubscribe_events", "subscription": 2}))
            except Exception:
                pass
            try:
                self.socket.close()
            except Exception:
                pass

    def output(self, kind, payload):
        self.emit({"session_id": self.session["id"], "id": str(uuid.uuid4()), "kind": kind, "payload": payload})

    def error(self, code):
        if not self.failed and not self.stopped.is_set():
            self.failed = True
            self.output("error", {"code": code})
        self.stop()

    def accept(self, message):
        if not isinstance(message, dict) or not isinstance(message.get("sequence"), int) or message["sequence"] <= self.sequence:
            return
        if message.get("kind") not in ("offer", "candidate") or not isinstance(message.get("payload"), dict):
            raise ValueError("Invalid camera signalling")
        self.messages.put_nowait(message)
        self.sequence = message["sequence"]

    def send_candidate(self, payload):
        if not self.ha_session:
            if len(self.pending) >= 160:
                raise ValueError("Too many pending candidates")
            self.pending.append(payload)
            return
        self.command_id += 1
        self.socket.send(json.dumps({"id": self.command_id, "type": "camera/webrtc/candidate", "entity_id": self.session["entity_id"], "session_id": self.ha_session, "candidate": payload}))

    def handle(self, message):
        if message.get("type") == "result" and message.get("id") == 1:
            if message.get("success") is not True:
                self.error("webrtc_unsupported")
                return
            config = message.get("result") or {}
            channel = config.get("dataChannel")
            if channel is not None and (not isinstance(channel, str) or len(channel) > 80):
                raise ValueError("Invalid camera configuration")
            self.output("config", {"data_channel": channel})
        elif message.get("type") == "result" and message.get("success") is False:
            self.error("connection_failed")
        elif message.get("type") == "event" and message.get("id") == 2:
            event = message.get("event") or {}
            kind = event.get("type")
            if kind == "session":
                session = event.get("session_id")
                if not isinstance(session, str) or not 1 <= len(session) <= 128:
                    raise ValueError("Invalid camera session")
                self.ha_session = session
                pending, self.pending = self.pending, []
                for candidate in pending:
                    self.send_candidate(candidate)
            elif kind == "answer":
                answer = event.get("answer")
                if not isinstance(answer, str) or not answer.startswith("v=0") or len(answer) > 65536:
                    raise ValueError("Invalid camera answer")
                self.output("answer", {"sdp": answer})
            elif kind == "candidate":
                candidate = event.get("candidate")
                if not isinstance(candidate, dict) or not isinstance(candidate.get("candidate"), str) or len(candidate["candidate"]) > 4096:
                    raise ValueError("Invalid camera candidate")
                allowed = {k: candidate[k] for k in ("candidate", "sdpMid", "sdpMLineIndex", "usernameFragment") if k in candidate}
                self.output("candidate", allowed)
            elif kind == "error":
                self.error("connection_failed")

    def run(self):
        try:
            entity = self.session["entity_id"]
            # The local allowlist is independent from cloud household permissions.
            if not ENTITY.fullmatch(entity) or entity not in self.connector.live_cameras or entity not in self.connector.allowlist:
                self.error("local_selection_required")
                return
            state = self.connector.ha("states/" + entity)
            if state.get("state") in ("unknown", "unavailable", "off"):
                self.error("camera_unavailable")
                return
            connect = self.connect
            if connect is None:
                from websockets.sync.client import connect
            url = "ws://supervisor/core/websocket" if self.connector.supervisor else self.connector.ha_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1) + "/websocket"
            with connect(url, proxy=None, compression=None, open_timeout=8, close_timeout=1, ping_interval=5, ping_timeout=5, max_size=150000, max_queue=32) as socket:
                self.socket = socket
                hello = json.loads(socket.recv(timeout=8))
                if hello.get("type") != "auth_required":
                    raise ValueError("Invalid Home Assistant greeting")
                socket.send(json.dumps({"type": "auth", "access_token": self.connector.ha_token}))
                if json.loads(socket.recv(timeout=8)).get("type") != "auth_ok":
                    raise ValueError("Home Assistant authentication ended")
                socket.send(json.dumps({"id": 1, "type": "camera/webrtc/get_client_config", "entity_id": entity}))
                while not self.stopped.is_set() and self.clock() < min(self.deadline, self.lease_until):
                    for _ in range(20):
                        try:
                            message = self.messages.get_nowait()
                        except queue.Empty:
                            break
                        payload = message["payload"]
                        if message["kind"] == "offer":
                            offer = payload.get("sdp")
                            if not receive_only_offer(offer):
                                raise ValueError("Invalid camera offer")
                            socket.send(json.dumps({"id": 2, "type": "camera/webrtc/offer", "entity_id": entity, "offer": offer}))
                        else:
                            self.send_candidate(payload)
                    try:
                        self.handle(json.loads(socket.recv(timeout=0.25)))
                    except TimeoutError:
                        continue
        except Exception:
            if not self.stopped.is_set():
                self.error("connection_lost")
        finally:
            self.stop()


class CameraRelay(threading.Thread):
    def __init__(self, connector, session_factory=CameraSession):
        super().__init__(name="homekind-camera-relay", daemon=True)
        self.connector, self.session_factory = connector, session_factory
        self.sessions = {}
        self.outgoing = collections.deque(maxlen=320)
        self.lock = threading.Lock()
        self.stopped = threading.Event()

    def emit(self, message):
        with self.lock:
            if len(self.outgoing) >= self.outgoing.maxlen:
                self.close_sessions()
                return
            self.outgoing.append(message)

    def close_sessions(self):
        for session in tuple(self.sessions.values()):
            session.stop()

    def stop(self):
        self.stopped.set()
        self.close_sessions()

    def exchange(self):
        with self.lock:
            batch = list(self.outgoing)[:20]
        body = {"bridge_id": self.connector.credentials["bridge_id"], "entities": sorted(self.connector.live_cameras), "cursors": {key: session.sequence for key, session in self.sessions.items()}, "signals": batch}
        result = self.connector.request(CAMERA_URL, self.connector.credentials["token"], body)
        if not isinstance(result, dict) or not isinstance(result.get("sessions"), list) or len(result["sessions"]) > 2:
            raise ValueError("Invalid camera connection response")
        with self.lock:
            for _ in batch:
                self.outgoing.popleft()
        alive = set()
        for value in result["sessions"]:
            if not isinstance(value, dict) or not IDENTIFIER.fullmatch(str(value.get("id", ""))) or value.get("entity_id") not in self.connector.live_cameras or value.get("entity_id") not in self.connector.allowlist:
                raise ValueError("Unselected camera requested")
            signals = value.get("signals", [])
            if not isinstance(signals, list) or len(signals) > 160:
                raise ValueError("Invalid camera signal list")
            key = value["id"]
            alive.add(key)
            if key not in self.sessions:
                self.sessions[key] = self.session_factory(self.connector, value, self.emit)
                self.sessions[key].start()
            session = self.sessions[key]
            session.renew()
            for message in signals:
                session.accept(message)
        for key in list(self.sessions):
            if key not in alive:
                self.sessions.pop(key).stop()

    def run(self):
        try:
            while not self.stopped.is_set() and not self.connector.stopping():
                try:
                    self.exchange()
                except Exception:
                    # Camera failures never stop sensor uploads or home commands.
                    self.close_sessions()
                    self.sessions.clear()
                    logging.warning("HomeKind live view is waiting for its camera connection.")
                self.stopped.wait(1 if self.sessions or self.outgoing else 8)
        finally:
            self.close_sessions()
            with self.lock:
                self.outgoing.clear()
