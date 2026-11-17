"""Read-only, expiring traces of one locally selected presence sensor.

Only fixed scalar fields leave the home. No MQTT publish, service calls, debug
logging changes, file access, permanent device selection or new privileges.
"""
import datetime as dt
import json
import math
import re
import threading
import time
from device_onboarding import LocalHome

ENUMS = {
    'motion_sensitivity': {'low', 'medium', 'high'},
    'presence_detection_options': {'both', 'mmwave', 'pir'},
    'ai_sensitivity_adaptive': {'ON', 'OFF'},
    'ai_interference_source_selfidentification': {'ON', 'OFF'},
}
NUMBERS = {'absence_delay_timer', 'pir_detection_interval', 'detection_range', 'target_distance', 'linkquality', 'battery'}
BOOLEANS = {'presence', 'pir_detection'}
STATES = {'on', 'off', 'unknown', 'unavailable'}


def values(payload):
    if not isinstance(payload, dict):
        return {}
    result = {}
    for key, value in payload.items():
        if key in ENUMS and isinstance(value, str) and value in ENUMS[key]:
            result[key] = value
        elif key in BOOLEANS and type(value) is bool:
            result[key] = value
        elif key in NUMBERS and type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 16777215:
            result[key] = value
    return result


def expiry(value):
    stamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('An explicit timezone is required.')
    return stamp.timestamp()


def checked(payload, options, now):
    if not options.get('managed_settings') or not options.get('management_id'):
        return None
    if not isinstance(payload, dict) or set(payload) != {'protocol', 'management_id', 'capture'} or type(payload['protocol']) is not int or payload['protocol'] != 1 or payload['management_id'] != options['management_id']:
        return None
    job = payload['capture']
    if job is None:
        return None
    if not isinstance(job, dict) or set(job) != {'id', 'entity_id', 'ha_device_id', 'ha_unique_id', 'expires_at', 'last_sequence'}:
        return None
    if (not isinstance(job['id'], str) or not re.fullmatch(r'[0-9a-f-]{36}', job['id'])
            or not isinstance(job['entity_id'], str) or not re.fullmatch(r'binary_sensor\.[a-z0-9_]{1,150}', job['entity_id'])
            or job['entity_id'] not in options.get('entity_ids', [])
            or not isinstance(job['ha_device_id'], str) or not re.fullmatch(r'[0-9a-f]{32}', job['ha_device_id'])
            or not isinstance(job['ha_unique_id'], str) or not 1 <= len(job['ha_unique_id']) <= 200
            or type(job['last_sequence']) is not int or not 0 <= job['last_sequence'] <= 200):
        return None
    try:
        if not now < expiry(job['expires_at']) <= now + 45:
            return None
    except (ValueError, TypeError, KeyError, OverflowError):
        return None
    return job


def state_values(row):
    if not isinstance(row, dict) or row.get('state') not in STATES:
        return {}
    result = {'state': row['state']}
    for key in ('last_changed', 'last_updated'):
        value = row.get(key)
        if isinstance(value, str) and len(value) <= 40 and re.fullmatch(r'\d{4}-\d{2}-\d{2}T[0-9:.+Z-]+', value):
            result[key] = value
    return result


def source_topic(info, unique):
    entries = info.get('entities', []) if isinstance(info, dict) else []
    if isinstance(entries, dict):
        entries = list(entries.values())
    if not isinstance(entries, list) or len(entries) > 500:
        return None
    topics = set()
    for entry in entries:
        config = entry.get('discovery_data', {}).get('payload', {}) if isinstance(entry, dict) else {}
        topic = config.get('state_topic') if isinstance(config, dict) else None
        if (config.get('unique_id') == unique and isinstance(topic, str) and 1 <= len(topic) <= 300
                and not re.search(r'[\x00-\x1f\x7f#+]', topic) and '/bridge/' not in topic
                and not topic.endswith(('/set', '/get'))):
            topics.add(topic)
    return next(iter(topics)) if len(topics) == 1 else None


class DeviceDiagnostics:
    def __init__(self, manager, home_factory=LocalHome, clock=time.time):
        self.manager, self.home_factory, self.clock = manager, home_factory, clock
        self.lock = threading.RLock()
        self.job = None
        self.worker = None
        self.samples = []
        self.snapshot = {}
        self.sequence = 0
        self.status = 'collecting'

    def prepare(self, options):
        with self.lock:
            report = {'protocol': 1, 'management_id': options.get('management_id', '')}
            if self.job and self.job.get('management_id') == options.get('management_id'):
                report.update(capture_id=self.job['id'], samples=self.samples[:40], snapshot=self.snapshot, status=self.status)
            return report

    def accept(self, payload, options, credentials):
        job = checked(payload, options, self.clock())
        with self.lock:
            if not job:
                self.job = None
                self.samples = []
                return
            if self.job and self.job['id'] == job['id']:
                self.job['expires_at'] = job['expires_at']
                self.samples = [s for s in self.samples if s['sequence'] > job['last_sequence']]
                return
            # A retiring worker must finish before another device can be inspected.
            if self.worker and self.worker.is_alive():
                self.job = None
                return
            self.job = {**job, 'management_id': options['management_id']}
            self.samples, self.snapshot, self.sequence, self.status = [], {}, job['last_sequence'], 'collecting'
            self.worker = threading.Thread(target=self.run, args=(job['id'], options.copy(), credentials.copy()), name='belmelo-device-check', daemon=True)
            self.worker.start()

    def current(self, ident, options, credentials):
        with self.lock:
            job = self.job
            if not job or job['id'] != ident or self.clock() >= expiry(job['expires_at']) or self.sequence >= 200:
                return False
        try:
            return (not self.manager.stopped.is_set() and self.manager.options() == options
                    and self.manager.connector is not None and not self.manager.connector.stopping()
                    and self.manager.connector.credentials == credentials
                    and job['entity_id'] in self.manager.connector.allowlist)
        except Exception:
            return False

    def append(self, ident, source, data, retained=False):
        if not data:
            return
        with self.lock:
            if not self.job or self.job['id'] != ident or self.clock() >= expiry(self.job['expires_at']) or self.sequence >= 200 or len(self.samples) >= 40:
                return
            self.sequence += 1
            self.samples.append({'sequence': self.sequence, 'at': dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc).isoformat().replace('+00:00', 'Z'), 'source': source, 'retained': retained is True, 'data': data})

    def run(self, ident, options, credentials):
        ws = None
        try:
            c = self.manager.connector
            home = self.home_factory(c)
            if not self.current(ident, options, credentials):
                return
            ws = home.socket()
            home.auth(ws)
            registry = home.query(ws, 1, 'config/entity_registry/list')
            devices = home.query(ws, 2, 'config/device_registry/list')
            with self.lock:
                job = dict(self.job)
            entity = next((r for r in registry if r.get('entity_id') == job['entity_id']), {})
            device = next((r for r in devices if r.get('id') == job['ha_device_id']), {})
            if entity.get('device_id') != job['ha_device_id'] or entity.get('unique_id') != job['ha_unique_id'] or entity.get('disabled_by'):
                raise ValueError('The selected device identity changed.')
            with self.lock:
                if not self.job or self.job['id'] != ident:
                    return
                self.snapshot = {target: str(device[key])[:80] for target, key in [('firmware', 'sw_version'), ('manufacturer', 'manufacturer'), ('model', 'model')] if device.get(key) and not re.search(r'[\x00-\x1f\x7f]', str(device[key]))}
                self.snapshot['mqtt_available'] = False
            # Read only fixed settings of this exact physical device. Other
            # local states, names, attributes and payload fields are discarded.
            settings = {}
            states = {s['entity_id']: s for s in c.ha('states', timeout=5) if isinstance(s, dict) and isinstance(s.get('entity_id'), str)}
            for r in registry:
                unique = r.get('unique_id', '')
                if r.get('device_id') != job['ha_device_id'] or not isinstance(unique, str):
                    continue
                for key in ENUMS.keys() | NUMBERS:
                    if unique.endswith('_' + key + '_zigbee2mqtt'):
                        v = states.get(r.get('entity_id'), {}).get('state')
                        if key in NUMBERS:
                            try:
                                v = float(v)
                            except (ValueError, TypeError):
                                continue
                        settings.update(values({key: v}))
            self.append(ident, 'settings', settings)
            topic = None
            if entity.get('platform') == 'mqtt' and self.current(ident, options, credentials):
                try:
                    topic = source_topic(home.query(ws, 3, 'mqtt/device/debug_info', device_id=job['ha_device_id']), job['ha_unique_id'])
                    if topic:
                        ws.send(json.dumps({'id': 4, 'type': 'mqtt/subscribe', 'topic': topic, 'qos': 0}))
                except Exception:
                    topic = None
            next_state, previous = 0, None
            while self.current(ident, options, credentials):
                if self.clock() >= next_state:
                    next_state = self.clock() + 10
                    row = c.ha('states/' + job['entity_id'], timeout=3)
                    data = state_values(row)
                    # Include repeated checks so an unchanged state can be
                    # distinguished from a gap in this diagnostic path.
                    self.append(ident, 'home_assistant', data)
                if not topic:
                    self.manager.stopped.wait(.5)
                    continue
                try:
                    message = json.loads(ws.recv(timeout=1))
                except TimeoutError:
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get('id') == 4 and message.get('type') == 'result':
                    with self.lock:
                        self.snapshot['mqtt_available'] = message.get('success') is True
                    if message.get('success') is not True:
                        topic = None
                event = message.get('event', {})
                raw = event.get('payload') if isinstance(event, dict) else None
                if message.get('id') == 4 and event.get('topic') == topic and isinstance(raw, str) and len(raw.encode()) <= 16000:
                    try:
                        data = values(json.loads(raw))
                    except (ValueError, TypeError):
                        continue
                    self.append(ident, 'zigbee2mqtt', data, event.get('retain') is True)
        except Exception:
            with self.lock:
                if self.job and self.job['id'] == ident:
                    self.status = 'unavailable'
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass
