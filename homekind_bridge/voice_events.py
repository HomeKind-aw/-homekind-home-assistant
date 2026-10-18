"""Inactive voice-pilot transport primitives. Never records speech or performs HA actions.

The live connector deliberately does not import this module until cloud binding and
transactional dispatch are implemented. The caller must obtain a fresh binding from
the authenticated HomeKind protocol before each enqueue and exchange.
"""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from datetime import datetime
from uuid import UUID as UUIDValue, uuid5

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
INTENTS = frozenset(("help", "room_lights_on", "checkin_okay"))
EVENT_KEYS = frozenset(("protocol", "event_id", "endpoint_id", "binding_id", "intent", "occurred_at"))
FEEDBACK = {
    "recognised": "I recognised your request. It has not been sent yet.",
    "pending": "Your request is waiting for HomeKind. No delivery is confirmed yet.",
    "queued": "HomeKind has queued your request. Completion is not confirmed yet.",
    "notification_accepted": "The notification service accepted your request. Your family has not confirmed it yet.",
    "family_acknowledged": "A chosen family contact has acknowledged your request.",
    "lights_confirmed": "Home Assistant reports that the selected lights are on.",
    "checkin_recorded": "HomeKind has recorded the response to the pending check-in.",
    "failed": "Your request could not be completed. Please use another way to ask.",
    "uncertain": "I cannot confirm what happened. Please check HomeKind before trying again.",
}
MAX_AGE = 30
MAX_FUTURE = 5
RECEIPT_RETENTION = 86400
MAX_ROWS = 1000
MAX_PENDING = 20
RECEIPT_INTERVAL = 5
FINAL_RECEIPTS = frozenset(("family_acknowledged", "lights_confirmed", "checkin_recorded", "failed"))


class VoiceError(ValueError):
    pass


def timestamp(value):
    if not isinstance(value, str) or not STAMP.fullmatch(value):
        raise VoiceError("Use an original UTC voice event time.")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError as error:
        raise VoiceError("Check the voice event time.") from error


def phrase_intent(text):
    """Exact finite grammar for a local recogniser; never extract a command from prose."""
    if not isinstance(text, str) or len(text) > 80:
        return None
    clean = " ".join(text.lower().replace("’", "'").strip().split())
    return {"i need a hand": "help", "turn on the lights": "room_lights_on",
            "i'm okay": "checkin_okay", "i am okay": "checkin_okay"}.get(clean)


def validate_event(event, now, allow_old=False):
    if not isinstance(event, dict) or set(event) != EVENT_KEYS or type(event.get("protocol")) is not int or event["protocol"] != 1:
        raise VoiceError("Unsupported voice event.")
    if any(not isinstance(event[k], str) or not UUID.fullmatch(event[k]) for k in ("event_id", "endpoint_id", "binding_id")):
        raise VoiceError("Check the voice endpoint identifiers.")
    if not isinstance(event["intent"], str) or event["intent"] not in INTENTS:
        raise VoiceError("Unsupported voice request.")
    age = now - timestamp(event["occurred_at"])
    if age < -MAX_FUTURE or not allow_old and age > MAX_AGE:
        raise VoiceError("The voice request has expired. Please try again.")
    return dict(event)


def event_from_ha(message, now):
    """Project one authenticated HA bus event into metadata only, with a stable ID.

    The caller must use the already-authenticated local HA websocket, not a public
    HTTP listener. HA context is used for deduplication only, never speaker identity.
    """
    if not isinstance(message, dict) or message.get('event_type') != 'homekind_voice_request':
        raise VoiceError('Unsupported local voice event.')
    data = message.get('data')
    context = message.get('context')
    if not isinstance(data, dict) or set(data) != {'protocol', 'endpoint_id', 'binding_id', 'intent'}:
        raise VoiceError('Unsupported local voice fields.')
    context_id = context.get('id') if isinstance(context, dict) else None
    if not isinstance(context_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{26,64}', context_id):
        raise VoiceError('The local voice event has no stable context.')
    if not isinstance(data.get('endpoint_id'), str) or not UUID.fullmatch(data['endpoint_id']):
        raise VoiceError('Check the local voice endpoint.')
    event = {**data, 'event_id': str(uuid5(UUIDValue(data['endpoint_id']), context_id)), 'occurred_at': message.get('time_fired')}
    return validate_event(event, now)


def validate_binding(binding, event, scope, now):
    """Binding is authenticated configuration, never event-supplied authority."""
    if not isinstance(binding, dict) or binding.get("protocol") != 1 or type(binding.get("protocol")) is not int:
        raise VoiceError("Voice setup is not available.")
    if binding.get("enabled") is not True or binding.get("muted") is not False or binding.get("consent_current") is not True:
        raise VoiceError("Voice requests are paused.")
    for key in ("bridge_id", "installation_id"):
        if binding.get(key) != scope[key]:
            raise VoiceError("The home connection changed. Check voice setup.")
    for key in ("endpoint_id", "binding_id"):
        if binding.get(key) != event[key]:
            raise VoiceError("This microphone is not linked to the selected room.")
    if type(binding.get("revision")) is not int or binding["revision"] < 1 or timestamp(binding.get("expires_at")) <= now:
        raise VoiceError("Refresh voice setup before sending a request.")
    allowed = binding.get("intents")
    if not isinstance(allowed, list) or any(not isinstance(x, str) or x not in INTENTS for x in allowed) or event["intent"] not in allowed:
        raise VoiceError("This voice request is not enabled.")
    return binding["revision"]


def receipt_status(receipt, event, revision):
    if not isinstance(receipt, dict) or set(receipt) != {"protocol", "event_id", "binding_id", "binding_revision", "status"}:
        raise VoiceError("Voice delivery is not confirmed.")
    if type(receipt["protocol"]) is not int or receipt["protocol"] != 1 or receipt["event_id"] != event["event_id"] or receipt["binding_id"] != event["binding_id"] or type(receipt["binding_revision"]) is not int or receipt["binding_revision"] != revision:
        raise VoiceError("The voice receipt does not match this request.")
    status = receipt["status"]
    allowed = {"help": {"queued", "notification_accepted", "family_acknowledged", "failed", "uncertain"},
               "room_lights_on": {"queued", "lights_confirmed", "failed", "uncertain"},
               "checkin_okay": {"queued", "checkin_recorded", "failed", "uncertain"}}
    if not isinstance(status, str) or status not in allowed[event["intent"]]:
        raise VoiceError("The voice receipt has no matching delivery evidence.")
    return status


class VoiceOutbox:
    """Bounded restart-safe metadata spool; transport retries require server deduplication.

    An ambiguous send is kept under the same event ID. The caller must never turn
    an exchange into a direct HA action. Scope changes cannot replay old events.
    """
    def __init__(self, directory, bridge_id, installation_id, clock=time.time):
        if any(not isinstance(x, str) or not UUID.fullmatch(x) for x in (bridge_id, installation_id)):
            raise VoiceError("Check the paired home connection.")
        self.scope = {"bridge_id": bridge_id, "installation_id": installation_id}
        self.clock = clock
        self.lock = threading.RLock()
        directory = Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink():
            raise VoiceError("Check the local voice storage directory.")
        self.lock_fd = os.open(directory / "voice-events.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(self.lock_fd)
            raise VoiceError("Another voice transport owns this local queue.") from error
        self.db = None
        try:
            self._open_database(directory)
        except (OSError, sqlite3.Error, VoiceError) as error:
            self.close()
            raise VoiceError("Voice storage could not be opened. Preserve the file and check the local installation.") from error

    def _open_database(self, directory):
        self.path = directory / "voice-events.sqlite3"
        if self.path.is_symlink():
            raise VoiceError("Check the local voice storage.")
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute("pragma journal_mode=DELETE")
        self.db.execute("pragma synchronous=FULL")
        self.db.executescript("""
            create table if not exists scope (id integer primary key check(id=1), value text not null);
            create table if not exists events (
                id text primary key, digest text not null, event text not null,
                revision integer not null, created real not null, state text not null,
                status text not null, attempts integer not null default 0);
        """)
        # Additive spool upgrade; keep original event IDs, attempts and scope.
        columns = {row[1] for row in self.db.execute("pragma table_info(events)")}
        with self.db:
            if "last_receipt_check" not in columns:
                self.db.execute("alter table events add column last_receipt_check real not null default 0")
            if "receipt_ended" not in columns:
                self.db.execute("alter table events add column receipt_ended integer not null default 0")
        scope = json.dumps(self.scope, sort_keys=True, separators=(",", ":"))
        previous = self.db.execute("select value from scope where id=1").fetchone()
        if previous and previous[0] != scope:
            raise VoiceError("Voice storage belongs to a different home connection.")
        with self.db:
            self.db.execute("insert or ignore into scope values(1,?)", (scope,))
        self.prune()

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None

    def prune(self):
        with self.lock, self.db:
            now = self.clock()
            self.db.execute("update events set state='closed',status=case when attempts>0 then 'uncertain' else 'failed' end where state='pending' and created<?", (now - MAX_AGE,))
            self.db.execute("delete from events where created<?", (now - RECEIPT_RETENTION,))

    def enqueue(self, event, binding):
        with self.lock:
            self.prune()
            event = validate_event(event, self.clock())
            revision = validate_binding(binding, event, self.scope, self.clock())
            body = json.dumps(event, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(body.encode()).hexdigest()
            with self.db:
                old = self.db.execute("select digest,revision,status from events where id=?", (event["event_id"],)).fetchone()
                if old:
                    if old[:2] != (digest, revision):
                        raise VoiceError("This voice event identifier was already used.")
                    return old[2]
                if self.db.execute("select count(*) from events").fetchone()[0] >= MAX_ROWS or self.db.execute("select count(*) from events where state='pending'").fetchone()[0] >= MAX_PENDING:
                    raise VoiceError("Voice requests are busy. Please use another way to ask.")
                self.db.execute("insert into events(id,digest,event,revision,created,state,status) values(?,?,?,?,?,'pending','recognised')", (event["event_id"], digest, body, revision, timestamp(event["occurred_at"])))
            return "recognised"

    def exchange_one(self, send, current_binding):
        """send(event, revision) must use the fixed authenticated idempotent route.

        The lock prevents overlapping workers. A receipt closes transport only;
        queued is not delivery. A future receipt poller must recheck consent too.
        """
        with self.lock:
            self.prune()
            row = self.db.execute("select id,event,revision,attempts from events where state='pending' order by created,id limit 1").fetchone()
            if not row:
                return None
            ident, body, revision, attempts = row
            event = json.loads(body)
            try:
                validate_event(event, self.clock())
                if validate_binding(current_binding(event["endpoint_id"]), event, self.scope, self.clock()) != revision:
                    raise VoiceError("Voice choices changed. Start a new request.")
            except VoiceError:
                status = 'uncertain' if attempts else 'failed'
                with self.db:
                    self.db.execute("update events set state='closed',status=?,receipt_ended=1 where id=?", (status, ident))
                return {"event_id": ident, "status": status}
            except (OSError, TimeoutError):
                return {"event_id": ident, "status": "pending"}
            with self.db:
                self.db.execute("update events set attempts=attempts+1,status='pending' where id=?", (ident,))
            try:
                status = receipt_status(send(dict(event), revision), event, revision)
            except (OSError, TimeoutError, VoiceError):
                return {"event_id": ident, "status": "pending"}
            with self.db:
                self.db.execute("update events set state='closed',status=?,receipt_ended=? where id=?", (status, int(status in FINAL_RECEIPTS), ident))
            return {"event_id": ident, "status": status}

    def poll_receipt(self, fetch_receipt, current_binding):
        """Recover evidence only; fetch_receipt(id, binding_id, revision) never sends.

        Check a fresh authenticated binding before each read. Round-robin checks
        are separated by five seconds per event, within one-day local retention.
        Return only changed evidence, avoiding repeated feedback on unchanged
        queued/provider-accepted states. No callback can actuate a device.
        """
        with self.lock:
            self.prune()
            now = self.clock()
            row = self.db.execute("""select id,event,revision,status from events
                where state='closed' and receipt_ended=0 and attempts>0
                and status in ('queued','notification_accepted','uncertain','pending')
                and last_receipt_check<=? order by last_receipt_check,created,id limit 1""",
                (now - RECEIPT_INTERVAL,)).fetchone()
            if not row:
                return None
            ident, body, revision, previous = row
            with self.db:
                self.db.execute("update events set last_receipt_check=? where id=?", (now, ident))
            event = json.loads(body)
            try:
                validate_event(event, now, allow_old=True)
                if validate_binding(current_binding(event["endpoint_id"]), event, self.scope, now) != revision:
                    raise VoiceError("Voice choices changed.")
            except VoiceError:
                # Even if consent later returns, this ended revision cannot
                # recover another binding's receipt or emit stale confirmation.
                with self.db:
                    self.db.execute("update events set status='uncertain',receipt_ended=1 where id=?", (ident,))
                return {"event_id": ident, "status": "uncertain"} if previous != "uncertain" else None
            except (OSError, TimeoutError):
                return None
            try:
                value = fetch_receipt(ident, event["binding_id"], revision)
                status = receipt_status(value, event, revision)
            except (OSError, TimeoutError, VoiceError):
                return None
            # Never regress a provider acceptance merely because a stale read
            # reports queued; actual failure or uncertainty may still replace it.
            if previous == "notification_accepted" and status == "queued":
                return None
            with self.db:
                self.db.execute("update events set status=?,receipt_ended=? where id=?", (status, int(status in FINAL_RECEIPTS), ident))
            return {"event_id": ident, "status": status} if status != previous else None

    def status(self, event_id):
        with self.lock:
            self.prune()
            row = self.db.execute("select status from events where id=?", (event_id,)).fetchone()
            return row[0] if row else None

    def feedback_record(self, event_id):
        """Return only the original persisted metadata/revision for local feedback."""
        with self.lock:
            self.prune()
            row = self.db.execute('select event,revision from events where id=?', (event_id,)).fetchone()
            return (json.loads(row[0]), row[1]) if row else None
