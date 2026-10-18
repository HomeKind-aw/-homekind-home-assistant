"""A separate Supervisor caller, fixed to the connector in this repository.

There is deliberately no general Supervisor proxy, shell, URL or slug input.
Every destructive dispatch is journalled before it is sent and never replayed.
"""
import hashlib
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

CLOUD = "https://saixlksmjzbakopvbsqv.supabase.co/functions/v1/homekind-home-bridge"
VERSION = re.compile(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}")
BACKUP = re.compile(r"[a-z0-9]{8,64}")
PAIR = re.compile(r"([0-9a-f-]{36})\.([0-9a-f]{64})")


class MaintenanceError(Exception):
    pass


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream, separators=(',', ':'))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def request(url, token, payload=None, timeout=25):
    data = None if payload is None else json.dumps(payload, separators=(',', ':')).encode()
    req = urllib.request.Request(url, data=data, headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
    # Reject redirects: neither the Supervisor token nor the cloud credential may follow them.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect).open(req, timeout=timeout) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise MaintenanceError('Response exceeds the maintenance limit.')
            return json.loads(raw)
    except (OSError, ValueError, urllib.error.HTTPError) as error:
        raise MaintenanceError('Maintenance request could not be confirmed.') from error


def job_finished(job):
    if not isinstance(job, dict) or job.get('errors'):
        raise MaintenanceError('The Supervisor job reported a failure.')
    children = job.get('child_jobs', [])
    if not isinstance(children, list):
        raise MaintenanceError('The Supervisor job could not be verified.')
    ready = job.get('done') is True
    for child in children:
        ready = job_finished(child) and ready
    return ready


class Companion:
    def __init__(self, directory='/data/homekind-maintenance', call=request, clock=time.time, options_path='/data/options.json'):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.path = self.directory / 'maintenance.json'
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {'records': {}}
        self.call, self.clock, self.options_path = call, clock, Path(options_path)
        self.token = os.environ.get('SUPERVISOR_TOKEN', '')
        self.target = None

    def save(self):
        atomic(self.path, self.data)

    def options(self):
        value = json.loads(self.options_path.read_text())
        if not isinstance(value, dict):
            raise MaintenanceError('Check local maintenance choices.')
        return value

    def supervisor(self, path, payload=None, timeout=25):
        result = self.call('http://supervisor'+path, self.token, payload, timeout=timeout)
        if not isinstance(result, dict) or result.get('result') != 'ok' or not isinstance(result.get('data'), dict):
            raise MaintenanceError('Supervisor did not confirm this maintenance request.')
        return result['data']

    def cloud(self, operation, payload):
        credential = self.data['credential']
        return self.call(CLOUD+'/maintenance', credential['token'], {'bridge_id': credential['id'], 'operation': operation, 'payload': payload})

    def enrol(self, options):
        code = str(options.get('pairing_code', '')).strip()
        if code and hashlib.sha256(code.encode()).hexdigest() != self.data.get('pairing_hash'):
            match = PAIR.fullmatch(code)
            if not match or str(uuid.UUID(match[1])) != match[1]:
                raise MaintenanceError('Use the maintenance code from HomeKind.')
            if self.data.get('active') and self.data['records'][self.data['active']]['phase'] not in ('healthy', 'recovered', 'failed', 'unknown'):
                raise MaintenanceError('Finish the current maintenance request before pairing again.')
            self.data['credential'] = {'id': match[1], 'token': match[2]}
            self.data['pairing_hash'] = hashlib.sha256(code.encode()).hexdigest()
            self.save()
        if not self.data.get('credential'):
            raise MaintenanceError('Pair the maintenance companion in HomeKind.')

    def discover(self):
        own = self.supervisor('/addons/self/info')
        match = re.fullmatch(r'([0-9a-f]{8})_homekind_maintenance', own.get('slug', ''))
        if not match:
            raise MaintenanceError('Install the companion from the same HomeKind repository.')
        target = match[1]+'_homekind_bridge'
        info = self.supervisor('/addons/'+target+'/info')
        if info.get('slug') != target or info.get('repository') != own.get('repository') or not own.get('repository'):
            raise MaintenanceError('The connector must belong to this HomeKind repository.')
        self.target = target
        return info

    def check_info(self, info, before=None, latest=None, require_running=True):
        if info.get('auto_update') is not False or require_running and info.get('state') != 'started':
            raise MaintenanceError('Use managed updates and start the connector before maintenance.')
        if before is not None and info.get('version') != before:
            raise MaintenanceError('The installed connector version changed.')
        if latest is not None and info.get('version_latest') != latest:
            raise MaintenanceError('The available release changed. Review it in HomeKind.')

    def valid_backup(self, slug, record):
        if not BACKUP.fullmatch(str(slug)):
            raise MaintenanceError('The local backup reference is invalid.')
        info = self.supervisor('/backups/'+slug+'/info')
        addons = info.get('addons', [])
        if info.get('type') != 'partial' or info.get('homeassistant') is not None or info.get('folders', []) != [] or len(addons) != 1 or addons[0].get('slug') != self.target or addons[0].get('version') != record['before_version'] or info.get('extra', {}).get('homekind_operation') != record['id'] or info.get('extra', {}).get('homekind_target') != self.target or info.get('protected') is not True or not (any(value in (None, '.local') for value in info.get('locations', [info.get('location')])) or '.local' in info.get('location_attributes', {})):
            raise MaintenanceError('This backup is not the recorded connector recovery point.')
        return info

    def post_once(self, record, phase, path, payload):
        record.update(phase=phase+'_dispatch', dispatch_at=self.clock())
        self.save()
        # A lost response leaves *_dispatch in durable storage. Never resend it.
        result = self.supervisor(path, payload)
        job = result.get('job_id')
        if not isinstance(job, str) or not re.fullmatch(r'[0-9a-f-]{32,36}', job):
            raise MaintenanceError('The Supervisor job reference was not confirmed.')
        record.update(phase=phase, job_id=job)
        self.save()

    def result(self):
        record = self.data['records'].get(self.data.get('active'))
        if not record:
            return None
        phase = record['phase']
        states = {'backup': 'backing_up', 'backup_done': 'backing_up', 'update': 'updating', 'restore': 'restoring', 'verify': 'verifying'}
        return {'id': record['id'], 'state': states.get(phase, phase if phase in ('healthy', 'recovered', 'failed', 'unknown') else 'unknown'), **({'backup_id': record['backup_id']} if record.get('backup_id') else {})}

    def accept(self, command, info):
        if not isinstance(command, dict) or set(command) - {'id', 'operation', 'target_version', 'before_version', 'backup_id', 'recovery_of', 'authorised'}:
            raise MaintenanceError('Unsupported maintenance command.')
        ident = command.get('id')
        if not isinstance(ident, str) or str(uuid.UUID(ident)) != ident or command.get('operation') not in ('update', 'recover') or not VERSION.fullmatch(str(command.get('target_version'))) or not VERSION.fullmatch(str(command.get('before_version'))):
            raise MaintenanceError('Unsupported maintenance command.')
        if ident in self.data['records']:
            return
        self.check_info(info, command['before_version'], command['target_version'] if command['operation'] == 'update' else None, require_running=command['operation'] == 'update')
        previous = None
        if command['operation'] == 'recover':
            previous = self.data['records'].get(command.get('recovery_of'))
            if not previous or previous.get('backup_id') != command.get('backup_id') or previous['before_version'] != command['target_version'] or self.clock()-previous['created_at'] > 7*86400:
                raise MaintenanceError('Recovery is limited to a recent locally recorded connector backup.')
            self.valid_backup(previous['backup_id'], previous)
        record = {key: command[key] for key in ('id', 'operation', 'target_version', 'before_version')}
        record.update(phase='authorizing', created_at=self.clock())
        if previous:
            record.update(backup_id=previous['backup_id'], backup_operation=previous['id'], password=previous['password'])
        else:
            record['password'] = secrets.token_hex(32)
        self.data['records'][ident] = record
        self.data['active'] = ident
        self.save()
        auth = self.cloud('authorize', {'id': ident})
        if auth.get('authorised') is not True:
            record['phase'] = 'failed'
            self.save()
            return
        if previous:
            record['phase'] = 'backup_done'
            self.save()
        else:
            self.post_once(record, 'backup', '/backups/new/partial', {'name': 'HomeKind connector '+command['before_version'], 'addons': [self.target], 'folders': [], 'homeassistant': False, 'location': '.local', 'password': record['password'], 'background': True, 'extra': {'homekind_operation': ident, 'homekind_target': self.target}})

    def advance(self, info, locally_enabled):
        record = self.data['records'].get(self.data.get('active'))
        if not record or record['phase'] in ('healthy', 'recovered', 'failed', 'unknown'):
            return
        phase = record['phase']
        if phase.endswith('_dispatch') or phase == 'authorizing':
            # Reconcile by observation only after an ambiguous dispatch.
            record['phase'] = 'unknown'
            self.save()
            return
        if self.clock()-record['created_at'] > 3600:
            record['phase'] = 'unknown'
            self.save()
            return
        if phase in ('backup', 'update', 'restore'):
            job = self.supervisor('/jobs/'+record['job_id'])
            if not job_finished(job):
                return
            if phase == 'backup':
                slug = job.get('reference')
                self.valid_backup(slug, record)
                record.update(phase='backup_done', backup_id=slug)
            else:
                record.update(phase='verify', verification_started=self.clock(), healthy_started=None)
            self.save()
            return
        if phase == 'backup_done':
            if not locally_enabled:
                record['phase'] = 'failed'
                self.save()
                return
            current = self.discover()
            self.check_info(current, record['before_version'], record['target_version'] if record['operation'] == 'update' else None, require_running=record['operation'] == 'update')
            source = self.data['records'].get(record.get('backup_operation'), record)
            self.valid_backup(record['backup_id'], source)
            self.cloud('report', self.report(current, True))
            auth = self.cloud('commit', {'id': record['id']})
            if auth.get('authorised') is not True:
                record['phase'] = 'failed'
                self.save()
                return
            # Check the repository latest again just before the fixed update endpoint.
            self.check_info(self.discover(), record['before_version'], record['target_version'] if record['operation'] == 'update' else None, require_running=record['operation'] == 'update')
            if self.options().get('allow_updates') is not True:
                record['phase'] = 'failed'
                self.save()
                return
            if record['operation'] == 'update':
                self.post_once(record, 'update', '/store/addons/'+self.target+'/update', {'backup': False, 'background': True})
            else:
                self.post_once(record, 'restore', '/backups/'+record['backup_id']+'/restore/partial', {'addons': [self.target], 'folders': [], 'homeassistant': False, 'location': '.local', 'password': record['password'], 'background': True})
        elif phase == 'verify':
            healthy = info.get('state') == 'started' and info.get('version') == record['target_version']
            if healthy:
                record['healthy_started'] = record.get('healthy_started') or self.clock()
                if self.clock()-record['healthy_started'] >= 60:
                    record['phase'] = 'healthy' if record['operation'] == 'update' else 'recovered'
            else:
                record['healthy_started'] = None
            self.save()

    def report(self, info, enabled):
        version, latest = info.get('version', ''), info.get('version_latest', '')
        if not VERSION.fullmatch(version) or not VERSION.fullmatch(latest):
            raise MaintenanceError('Check the installed connector version.')
        return {'protocol': 1, 'ready': enabled and info.get('auto_update') is False, 'auto_update': info.get('auto_update') is not False, 'connector_running': info.get('state') == 'started', 'connector_version': version, 'latest_version': latest, **({'result': self.result()} if self.result() else {})}

    def step(self):
        options = self.options()
        self.enrol(options)
        info = self.discover()
        enabled = options.get('allow_updates') is True
        try:
            self.advance(info, enabled)
        except MaintenanceError:
            record = self.data['records'].get(self.data.get('active'))
            if record and record['phase'] not in ('healthy', 'recovered', 'failed', 'unknown'):
                record['phase'] = 'unknown' if record['phase'].endswith('_dispatch') or record['phase'] in ('update', 'restore') else 'failed'
                self.save()
        response = self.cloud('report', self.report(info, enabled))
        command = response.get('command')
        active = self.data['records'].get(self.data.get('active'))
        if command and enabled and (not active or active['phase'] in ('healthy', 'recovered', 'failed', 'unknown')):
            self.accept(command, info)
        try:
            from .window_runtime import WindowRuntime
            if not hasattr(self, 'window_runtime'):
                self.window_runtime = WindowRuntime(self)
            self.window_runtime.step()
        except Exception:
            # Optional window setup cannot stop the existing recovery lane.
            pass


def main():
    companion = Companion()
    while True:
        try:
            companion.step()
        except Exception:
            # Avoid logging credentials, raw Supervisor responses or sensor data.
            print('Maintenance waiting. Check HomeKind connection health or local setup.', flush=True)
        time.sleep(15)


if __name__ == '__main__':
    main()
