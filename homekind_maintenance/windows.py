"""Inactive household-window foundation; no cloud, Supervisor or update calls.

A schedule decision is not authority to dispatch. The future executor must still
verify current household permission, local opt-in, backup and component readiness.
Nothing imports this module in the released Companion or connector.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .maintenance import atomic

UTC = timezone.utc
KEYS = {'schema', 'revision', 'enabled', 'zone', 'days', 'start', 'end'}


class WindowError(ValueError):
    pass


def instant(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise WindowError('A verified timezone-aware clock is required.')
    return value.astimezone(UTC)


def minutes(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', value):
        raise WindowError('Choose a local start and end time.')
    hour, minute = map(int, value.split(':'))
    return hour * 60 + minute


def policy(value):
    if not isinstance(value, dict) or set(value) != KEYS or value.get('schema') != 1 or type(value['schema']) is not int:
        raise WindowError('Unsupported update-window policy.')
    if type(value['revision']) is not int or not 1 <= value['revision'] <= 2147483647 or type(value['enabled']) is not bool:
        raise WindowError('Check the update-window revision and choice.')
    days = value['days']
    if not isinstance(days, list) or not 1 <= len(days) <= 7 or any(type(d) is not int or d not in range(7) for d in days) or len(set(days)) != len(days):
        raise WindowError('Choose at least one unique day, Monday 0 to Sunday 6.')
    zone = value['zone']
    if not isinstance(zone, str) or len(zone) > 64 or not re.fullmatch(r'[A-Za-z0-9_+\-]+(?:/[A-Za-z0-9_+\-]+)*', zone):
        raise WindowError('Use a supported household timezone.')
    try:
        ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise WindowError('Household timezone data is unavailable.') from error
    start, end = minutes(value['start']), minutes(value['end'])
    if not 1 <= (end - start) % 1440 <= 720:
        raise WindowError('Choose a window longer than zero and no longer than 12 hours.')
    return {**value, 'days': sorted(days)}


def boundary(day, minute, zone, later):
    naive = datetime(day.year, day.month, day.day) + timedelta(minutes=minute)
    candidates = set()
    for fold in (0, 1):
        utc = naive.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if utc.astimezone(zone).replace(tzinfo=None) == naive:
            candidates.add(utc)
    # Skip a nonexistent spring-forward boundary. For a repeated clock hour,
    # start later and end earlier so ambiguity cannot extend permission to run.
    return (max(candidates) if later else min(candidates)) if candidates else None


def occurrences(value, now):
    value, now = policy(value), instant(now)
    zone = ZoneInfo(value['zone'])
    today = now.astimezone(zone).date()
    identity = {key: value[key] for key in ('schema', 'zone', 'start', 'end')}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:20]
    result = []
    for offset in range(-1, 15):
        day = today + timedelta(days=offset)
        if day.weekday() not in value['days']:
            continue
        start = boundary(day, minutes(value['start']), zone, True)
        end_day = day + timedelta(days=minutes(value['end']) < minutes(value['start']))
        end = boundary(end_day, minutes(value['end']), zone, False)
        if start is None or end is None or end <= start or end <= now:
            continue
        result.append({'id': fingerprint + ':' + day.isoformat(), 'start': start, 'end': end})
    return sorted(result, key=lambda row: row['start'])


def evaluate(value, now, *, estimate_seconds, locally_enabled, authority_current,
             service_ready, active_use, paused=False, defer_until=None,
             last_occurrence=None, last_started_at=None, inflight=None):
    """Return eligibility only. A missed window never creates a catch-up job."""
    value, now = policy(value), instant(now)
    if type(estimate_seconds) is not int or not 1 <= estimate_seconds <= 43200:
        raise WindowError('Use a bounded positive update estimate.')
    if type(paused) is not bool:
        raise WindowError('Check the local pause choice.')
    until = instant(defer_until) if defer_until is not None else None
    prior = instant(last_started_at) if last_started_at is not None else None
    windows = occurrences(value, now)
    current = next((w for w in windows if w['start'] <= now < w['end']), None)
    upcoming = next((w for w in windows if w['start'] > now), None)
    reason = None
    if not value['enabled'] or locally_enabled is not True:
        reason = 'local_opt_out'
    elif authority_current is not True:
        reason = 'permission_unverified'
    elif paused:
        reason = 'paused'
    elif until is not None and now < until:
        reason = 'deferred'
    elif prior is not None and now < prior:
        reason = 'clock_rollback'
    elif inflight:
        reason = 'previous_outcome_unknown' if inflight.get('phase') == 'unknown' else 'update_in_progress'
    elif service_ready is not True or active_use is not False:
        reason = 'home_not_ready'
    elif current is None:
        reason = 'outside_window'
    elif current['id'] == last_occurrence:
        reason = 'already_completed'
    elif (current['end'] - now).total_seconds() < estimate_seconds + 300:
        reason = 'insufficient_time'
    return {'eligible': reason is None, 'reason': reason or 'window_open',
            'occurrence': current, 'next': upcoming}


class WindowJournal:
    """Private additive candidate journal. Unknown/corrupt files fail closed.

    A reservation survives restart; no method performs or retries an update.
    The existing HomeKind-only Companion journal and options are untouched.
    """
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock_path = self.path.with_suffix('.lock')

    @contextmanager
    def locked(self):
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def read(self):
        if not self.path.exists():
            return None
        try:
            if self.path.stat().st_size > 65536:
                raise WindowError('The window journal exceeds its limit.')
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict) or set(data) != {'schema', 'policy', 'paused', 'defer_until', 'last_occurrence', 'last_started_at', 'inflight'} or type(data['schema']) is not int or data['schema'] != 1 or type(data['paused']) is not bool:
                raise WindowError('Unsupported window journal; retain it for review.')
            data['policy'] = policy(data['policy'])
            for key in ('defer_until', 'last_started_at'):
                if data[key] is not None:
                    instant(datetime.fromisoformat(data[key]))
            record = data['inflight']
            if record is not None and (not isinstance(record, dict) or set(record) != {'operation_id', 'phase', 'occurrence'} or record['phase'] not in ('reserved', 'unknown') or str(uuid.UUID(record['operation_id'])) != record['operation_id'] or not isinstance(record['occurrence'], str)):
                raise WindowError('Retain the unverified maintenance operation.')
            if data['last_occurrence'] is not None and not isinstance(data['last_occurrence'], str):
                raise WindowError('Retain the window receipt for review.')
            return data
        except (OSError, TypeError, ValueError, KeyError) as error:
            raise WindowError('Window journal could not be checked; no update is eligible.') from error

    def configure(self, value, expected_revision):
        value = policy(value)
        with self.locked():
            data = self.read()
            old = data['policy']['revision'] if data else 0
            if type(expected_revision) is not int or expected_revision != old or value['revision'] != old + 1:
                raise WindowError('The household update choice changed. Refresh it first.')
            if data is None:
                data = {'schema': 1, 'paused': False, 'defer_until': None,
                        'last_occurrence': None, 'last_started_at': None, 'inflight': None}
            data['policy'] = value
            atomic(self.path, data)

    def hold(self, *, expected_revision, paused, defer_until=None):
        if type(paused) is not bool:
            raise WindowError('Choose a valid pause state.')
        until = instant(defer_until).isoformat() if defer_until is not None else None
        with self.locked():
            data = self.read()
            if type(expected_revision) is not int or data is None or data['policy']['revision'] != expected_revision:
                raise WindowError('Refresh the household update choice.')
            data.update(paused=paused, defer_until=until)
            data['policy']['revision'] += 1
            atomic(self.path, data)

    def reconcile(self, snapshot, *, expected_revision, locally_enabled, authority_current):
        """Inactive settings adapter: accept exact, current, monotonic choices.

        Revisions can skip when an offline home missed several cloud edits.
        This never clears a physical operation or changes the old updater.
        """
        if locally_enabled is not True or authority_current is not True:
            raise WindowError('Current local choice and household authority are required.')
        if not isinstance(snapshot, dict) or set(snapshot) != {'policy', 'paused', 'defer_until'} or type(snapshot['paused']) is not bool:
            raise WindowError('Use a compatible update-window snapshot.')
        value = policy(snapshot['policy'])
        defer = snapshot['defer_until']
        if defer is not None:
            if not isinstance(defer, str):
                raise WindowError('Check the defer date.')
            try:
                defer = instant(datetime.fromisoformat(defer)).isoformat()
            except ValueError as error:
                raise WindowError('Check the defer date.') from error
        with self.locked():
            data = self.read()
            old = data['policy']['revision'] if data else 0
            if type(expected_revision) is not int or expected_revision != old or value['revision'] < old:
                raise WindowError('Refresh the local update-window revision.')
            if value['revision'] == old:
                if data['policy'] != value or data['paused'] != snapshot['paused'] or data['defer_until'] != defer:
                    raise WindowError('A revision cannot change its saved choices.')
                return
            if data is None:
                data = {'schema': 1, 'last_occurrence': None, 'last_started_at': None, 'inflight': None}
            data.update(policy=value, paused=snapshot['paused'], defer_until=defer)
            atomic(self.path, data)

    def reserve(self, operation_id, now, *, expected_revision, **context):
        if not isinstance(operation_id, str) or str(uuid.UUID(operation_id)) != operation_id:
            raise WindowError('Use a new bounded maintenance operation.')
        with self.locked():
            data = self.read()
            if type(expected_revision) is not int or data is None or data['policy']['revision'] != expected_revision:
                raise WindowError('Refresh the household update choice.')
            verdict = evaluate(data['policy'], now, **context, paused=data['paused'],
                               defer_until=datetime.fromisoformat(data['defer_until']) if data['defer_until'] else None,
                               last_occurrence=data['last_occurrence'],
                               last_started_at=datetime.fromisoformat(data['last_started_at']) if data['last_started_at'] else None,
                               inflight=data['inflight'])
            if verdict['eligible']:
                data['inflight'] = {'operation_id': operation_id, 'phase': 'reserved',
                                    'occurrence': verdict['occurrence']['id']}
                data['last_started_at'] = instant(now).isoformat()
                atomic(self.path, data)
            return verdict

    def finish(self, operation_id, *, verified):
        if type(verified) is not bool:
            raise WindowError('Use a verified operation outcome.')
        with self.locked():
            data = self.read()
            if data is None or not data['inflight'] or data['inflight']['operation_id'] != operation_id:
                raise WindowError('The operation does not match this journal.')
            if verified:
                data['last_occurrence'] = data['inflight']['occurrence']
                data['inflight'] = None
            else:
                data['inflight']['phase'] = 'unknown'
            atomic(self.path, data)

    def cancel_before_update(self, operation_id):
        """Consume a window after a verified pre-update cancellation.

        Only the locked executor may call this while no Core/OS/reboot request
        has been dispatched. It records no successful update or recovery.
        """
        with self.locked():
            data = self.read()
            if data is None or not data['inflight'] or data['inflight']['operation_id'] != operation_id or data['inflight']['phase'] != 'reserved':
                raise WindowError('Retain the unverified maintenance reservation.')
            data['last_occurrence'] = data['inflight']['occurrence']
            data['inflight'] = None
            atomic(self.path,data)
