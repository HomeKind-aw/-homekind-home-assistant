"""Fixed Core/OS window executor; no cloud URL, service, slug or version input.

Every operation is durably fenced before dispatch. Ambiguous dispatch is never
repeated. Backups are retained for an explicit installer recovery, never restored
automatically over later resident choices.
"""
from datetime import datetime, timezone
import json
import re
import secrets
import uuid
import fcntl
import os
from .maintenance import atomic, MaintenanceError, BACKUP, job_finished
from .windows import WindowError, evaluate

VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,79}')
JOB = re.compile(r'[0-9a-f]{32}')
ACTIVE = {'preparing', 'backup', 'backup_done', 'update', 'os_ready', 'verify'}
# Evaluate locally: no entity values, listening data or activity history leave HA.
IDLE_TEMPLATE = "{{ (states.media_player | selectattr('state','in',['playing','buffering']) | list | count == 0 and states.script | selectattr('state','eq','on') | list | count == 0) | to_json }}"


class SystemUpdater:
    def __init__(self, companion, journal, authorise=lambda: False):
        self.c, self.journal, self.authorise = companion, journal, authorise
        self.path = companion.directory / 'system-update.json'
        self.job = self.read()

    def read(self):
        if not self.path.exists():
            return None
        try:
            if self.path.stat().st_size > 16384:
                raise ValueError()
            job = json.loads(self.path.read_text())
            keys = {'schema','id','component','before','target','core_before','revision','created_at','phase','password','backup_id','job_id','healthy_since','scope','stage'}
            if not isinstance(job, dict) or set(job) != keys or type(job['schema']) is not int or job['schema'] != 1 or str(uuid.UUID(job['id'])) != job['id'] or job['component'] not in ('core','os') or job['phase'] not in ACTIVE | {'backup_dispatch','update_dispatch','reboot_dispatch','healthy','unknown','failed','cancelled'} or any(not isinstance(job[k],str) or not VERSION.fullmatch(job[k]) for k in ('before','target','core_before')) or not isinstance(job['password'],str) or not re.fullmatch(r'[0-9a-f]{64}',job['password']) or not isinstance(job['scope'],str) or not re.fullmatch(r'[0-9a-f]{64}',job['scope']) or type(job['revision']) is not int or job['revision']<1 or type(job['created_at']) not in (int,float) or job['stage'] not in ('preparing','backup','update','reboot','verify') or job['backup_id'] is not None and (not isinstance(job['backup_id'],str) or not BACKUP.fullmatch(job['backup_id'])) or job['job_id'] is not None and (not isinstance(job['job_id'],str) or not JOB.fullmatch(job['job_id'])) or job['healthy_since'] is not None and type(job['healthy_since']) not in (int,float):
                raise ValueError()
            return job
        except (OSError,ValueError,TypeError,KeyError):
            raise WindowError('Retain the unverified system-update record.') from None

    def save(self):
        atomic(self.path, self.job)

    def local(self):
        import hashlib
        options = self.c.options()
        credential = self.c.data.get('credential', {})
        scope = hashlib.sha256((str(credential.get('id',''))+':'+str(credential.get('token',''))).encode()).hexdigest()
        return options.get('allow_updates') is True and options.get('allow_system_updates') is True and bool(credential), scope

    def ready(self):
        s = self.c.supervisor('/supervisor/info', timeout=2)
        core = self.c.supervisor('/core/info', timeout=2)
        if s.get('healthy') is not True or s.get('supported') is not True or s.get('channel') != 'stable' or not isinstance(core.get('version'),str) or not VERSION.fullmatch(core['version']):
            return False
        idle = self.c.call('http://supervisor/core/api/template', self.c.token, {'template': IDLE_TEMPLATE}, timeout=2)
        return idle is True

    def eligible(self, estimate=1800):
        enabled, scope = self.local()
        data = self.journal.read()
        if not enabled or not data or self.job and self.job['scope'] != scope:
            return False
        if self.job and data['policy']['revision'] != self.job['revision']:
            return False
        if not self.authorise():
            return False
        # Re-read after cloud permission and before each disruptive request.
        enabled, current = self.local()
        if not enabled or current != scope or not self.ready():
            return False
        data = self.journal.read()
        verdict = evaluate(data['policy'],datetime.fromtimestamp(self.c.clock(),timezone.utc),
            estimate_seconds=estimate,locally_enabled=True,authority_current=True,
            service_ready=True,active_use=False,paused=data['paused'],
            defer_until=datetime.fromisoformat(data['defer_until']) if data['defer_until'] else None,
            last_occurrence=data['last_occurrence'],
            last_started_at=datetime.fromisoformat(data['last_started_at']) if data['last_started_at'] else None,
            inflight=None)
        return verdict['eligible']

    def backup(self):
        j = self.job
        if not isinstance(j['backup_id'],str) or not BACKUP.fullmatch(j['backup_id']):
            raise MaintenanceError('A verified backup is required.')
        b = self.c.supervisor('/backups/'+j['backup_id']+'/info', timeout=2)
        locations = b.get('locations')
        local = isinstance(locations,list) and any(x in ('.local',None) for x in locations)
        attrs = b.get('location_attributes',{}).get('.local')
        if b.get('slug') != j['backup_id'] or b.get('type') != 'full' or b.get('protected') is not True or b.get('homeassistant') != j['core_before'] or b.get('extra',{}).get('homekind_operation') != j['id'] or not local or isinstance(attrs,dict) and attrs.get('protected') is not True or b.get('homeassistant_exclude_database') is not False:
            raise MaintenanceError('The complete encrypted local backup could not be verified.')

    def post_once(self, stage, path, payload):
        j = self.job
        j.update(phase=stage+'_dispatch',stage=stage,job_id=None)
        self.save()
        response = self.c.supervisor(path,payload)
        # A reboot can terminate its HTTP response. The persisted dispatch fence
        # remains; restart verification observes the version without rebooting twice.
        if stage == 'reboot':
            j.update(phase='verify',healthy_since=None,stage='verify')
        elif stage == 'update' and j['component'] == 'os':
            j.update(phase='os_ready')
        else:
            jid = response.get('job_id')
            if not isinstance(jid,str) or not JOB.fullmatch(jid):
                raise MaintenanceError('The maintenance job could not be confirmed.')
            j.update(phase=stage,job_id=jid)
        self.save()

    def status(self):
        j = self.job
        return None if not j else {k:j[k] for k in ('id','component','before','target','phase','created_at','backup_id')}

    def cancel(self):
        if self.job['phase'] not in ('preparing','backup_done'):
            raise WindowError('A dispatched update cannot be cancelled automatically.')
        self.journal.cancel_before_update(self.job['id'])
        self.job['phase']='cancelled'
        self.save()

    def step(self):
        fd = os.open(self.path.with_suffix('.lock'), os.O_RDWR | os.O_CREAT | getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            try:
                fcntl.flock(fd,fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            self.job = self.read()
            self._step()
        finally:
            os.close(fd)

    def _step(self):
        # Reload under the process lock. A second executor cannot dispatch the
        # same pre-operation phase; the reservation also survives restart.
        j = self.job
        if j and j['phase'] == 'failed':
            return
        if j and j['phase'] == 'unknown':
            # Recovery is observation only: never repeat an uncertain POST.
            if self.local()[1] != j['scope'] or j['stage'] not in ('update','reboot'):
                return
            try:
                info=self.c.supervisor('/'+j['component']+'/info',timeout=2)
                if info.get('version') == j['target']:
                    j.update(phase='verify',stage='verify',healthy_since=None);self.save()
                elif j['component']=='os' and j['stage']=='update' and info.get('version')==j['before'] and info.get('version_pending')==j['target']:
                    j['phase']='os_ready';self.save()
            except (MaintenanceError,OSError):
                pass
            return
        if j and j['phase'] in ('healthy','cancelled'):
            self.job = None
        try:
            if self.job is None:
                data = self.journal.read()
                if not data or data['inflight'] or not self.eligible():
                    return
                core = self.c.supervisor('/core/info', timeout=2)
                for component in ('core','os'):
                    info = core if component == 'core' else self.c.supervisor('/os/info', timeout=2)
                    before,target = info.get('version'),info.get('version_latest')
                    if any(not isinstance(v,str) or not VERSION.fullmatch(v) for v in (before,target)) or before == target or info.get('version_pending'):
                        continue
                    ident = str(uuid.uuid4())
                    verdict = self.journal.reserve(ident,datetime.fromtimestamp(self.c.clock(),timezone.utc),
                        expected_revision=data['policy']['revision'],estimate_seconds=1800,
                        locally_enabled=True,authority_current=True,service_ready=True,active_use=False)
                    if not verdict['eligible']:
                        return
                    self.job = dict(schema=1,id=ident,component=component,before=before,target=target,
                        core_before=core['version'],revision=data['policy']['revision'],created_at=self.c.clock(),
                        phase='preparing',stage='preparing',password=secrets.token_hex(32),backup_id=None,
                        job_id=None,healthy_since=None,scope=self.local()[1])
                    self.save()
                    return
                return
            j = self.job
            if j['phase'].endswith('_dispatch'):
                if j['stage'] in ('update','reboot'):
                    # Observation only after an ambiguous disruptive request.
                    info = self.c.supervisor('/'+j['component']+'/info',timeout=2)
                    if info.get('version') == j['target']:
                        j.update(phase='verify',stage='verify',healthy_since=None); self.save(); return
                j['phase']='unknown';self.save();self.journal.finish(j['id'],verified=False);return
            if self.c.clock() < j['created_at'] or self.c.clock()-j['created_at']>7200:
                if j['phase'] in ('preparing','backup_done'):
                    self.cancel();return
                raise MaintenanceError('The maintenance outcome needs review.')
            if j['phase'] == 'preparing':
                # Give the cloud maintenance fence and any already dispatched
                # short control leases time to settle before taking HA offline.
                if self.c.clock()-j['created_at'] < 30:
                    return
                if not self.eligible():
                    self.cancel();return
                self.post_once('backup','/backups/new/full',{'name':'HomeKind before '+j['component']+' '+j['before'],
                    'password':j['password'],'location':'.local','homeassistant_exclude_database':False,'background':True,'extra':{'homekind_operation':j['id']}})
            elif j['phase'] in ('backup','update'):
                progress = self.c.supervisor('/jobs/'+j['job_id'],timeout=2)
                if not job_finished(progress):
                    return
                if j['phase'] == 'backup':
                    j['backup_id']=progress.get('reference');self.backup();j['phase']='backup_done'
                else:
                    j.update(phase='verify',stage='verify',healthy_since=None)
                self.save()
            elif j['phase'] == 'backup_done':
                if not self.eligible():
                    self.cancel();return
                self.backup()
                info = self.c.supervisor('/'+j['component']+'/info',timeout=2)
                if info.get('version') != j['before'] or info.get('version_latest') != j['target'] or info.get('version_pending'):
                    raise MaintenanceError('The available update changed; review it first.')
                if j['component'] == 'core':
                    self.c.supervisor('/core/check',{},timeout=25)
                if not self.eligible():
                    return
                payload = {'version':j['target']}
                if j['component'] == 'core': payload.update(backup=False,background=True)
                self.post_once('update','/'+j['component']+'/update',payload)
            elif j['phase'] == 'os_ready':
                info = self.c.supervisor('/os/info',timeout=2)
                if info.get('version_pending') != j['target'] or info.get('version') != j['before']:
                    raise MaintenanceError('Check the pending OS update before restarting.')
                if self.eligible(900):
                    self.backup();self.post_once('reboot','/host/reboot',{})
            elif j['phase'] == 'verify':
                info = self.c.supervisor('/'+j['component']+'/info',timeout=2)
                if info.get('version') != j['target'] or not self.ready():
                    j['healthy_since']=None
                else:
                    j['healthy_since']=j['healthy_since'] if j['healthy_since'] is not None else self.c.clock()
                    if self.c.clock()-j['healthy_since']>=60:
                        self.journal.finish(j['id'],verified=True);j['phase']='healthy'
                self.save()
        except (MaintenanceError,WindowError,OSError,ValueError,KeyError,TypeError):
            if self.job:
                self.job['phase']='unknown' if self.job['phase'].endswith('_dispatch') or self.job['phase'] in ('update','verify','os_ready') else 'failed'
                self.save();self.journal.finish(self.job['id'],verified=False)
