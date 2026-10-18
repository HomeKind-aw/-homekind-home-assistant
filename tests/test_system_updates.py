from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from homekind_maintenance.system_updates import SystemUpdater,IDLE_TEMPLATE
from homekind_maintenance.maintenance import MaintenanceError
from homekind_maintenance.windows import WindowJournal,WindowError


class SystemUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.now=datetime(2026,10,18,15,5,tzinfo=timezone.utc).timestamp()  # Mon02:05 Melbourne
        self.options={'allow_updates':True,'allow_system_updates':True}
        self.authority=True;self.idle=True;self.posts=[];self.fail_route=None;self.bad_backup=False
        self.core={'version':'2026.10.1','version_latest':'2026.10.2'}
        self.os={'version':'16.1','version_latest':'16.2','version_pending':None}
        self.extra={};self.c=SimpleNamespace(directory=Path(self.temp.name),clock=lambda:self.now,
            options=lambda:self.options,data={'credential':{'id':'test-only','token':'a'*64}},
            token='local-only',supervisor=self.supervisor,call=self.call)
        self.journal=WindowJournal(Path(self.temp.name)/'window.json')
        self.journal.configure(dict(schema=1,revision=1,enabled=True,zone='Australia/Melbourne',days=list(range(7)),start='02:00',end='05:00'),0)
        self.engine=self.new()

    def new(self): return SystemUpdater(self.c,self.journal,lambda:self.authority)

    def call(self,url,token,payload,timeout):
        self.assertEqual(url,'http://supervisor/core/api/template');self.assertEqual(payload,{'template':IDLE_TEMPLATE})
        self.assertEqual(timeout,2);return self.idle

    def supervisor(self,path,payload=None,timeout=25):
        if payload is not None:
            self.posts.append((path,dict(payload)))
            if path=='/backups/new/full': self.extra=dict(payload['extra'])
            if path=='/core/update': self.core['version']=self.core['version_latest']
            if path=='/os/update': self.os['version_pending']=self.os['version_latest']
            if path=='/host/reboot': self.os.update(version=self.os['version_pending'],version_pending=None)
            if path==self.fail_route: raise MaintenanceError('lost reply')
            if path=='/backups/new/full': return {'job_id':'b'*32}
            if path=='/core/update': return {'job_id':'c'*32}
            return {}
        if path=='/supervisor/info': return {'healthy':True,'supported':True,'channel':'stable'}
        if path=='/core/info': return dict(self.core)
        if path=='/os/info': return dict(self.os)
        if path=='/jobs/'+'b'*32: return {'done':True,'errors':[],'child_jobs':[],'reference':'abcd1234'}
        if path=='/jobs/'+'c'*32: return {'done':True,'errors':[],'child_jobs':[]}
        if path=='/backups/abcd1234/info':
            return {'slug':'abcd1234','type':'full','protected':not self.bad_backup,
                'homeassistant':'2026.10.1','homeassistant_exclude_database':False,
                'extra':self.extra,'locations':['.local'],'location_attributes':{'.local':{'protected':True}}}
        raise AssertionError('Unexpected fixed route '+path)

    def through_backup(self):
        self.engine.step();self.assertEqual(self.engine.job['phase'],'preparing')
        self.now+=31;self.engine.step();self.engine.step()
        self.assertEqual(self.engine.job['phase'],'backup_done')

    def test_core_backup_single_dispatch_restart_and_real_health_verification(self):
        self.through_backup();self.engine=self.new();self.engine.step()
        self.assertEqual(self.engine.job['phase'],'update')
        self.engine.step();self.engine.step();self.now+=61;self.engine.step()
        self.assertEqual(self.engine.job['phase'],'healthy')
        self.assertIsNone(self.journal.read()['inflight'])
        for _ in range(3): self.engine.step()
        self.assertEqual([p for p,b in self.posts].count('/core/update'),1)
        self.assertEqual(self.posts[0][0],'/backups/new/full')
        self.assertEqual(len(self.posts[0][1]['password']),64)
        self.assertFalse(self.posts[0][1]['homeassistant_exclude_database'])

    def test_local_or_cloud_opt_out_active_use_and_outside_window_never_dispatch(self):
        for flag in ('allow_updates','allow_system_updates'):
            self.options[flag]=False;self.engine.step();self.assertIsNone(self.engine.job);self.options[flag]=True
        self.authority=False;self.engine.step();self.assertIsNone(self.engine.job)
        self.authority=True;self.idle=False;self.engine.step();self.assertIsNone(self.engine.job)
        self.idle=True;self.now+=36000;self.engine.step();self.assertIsNone(self.engine.job)
        self.assertEqual(self.posts,[])

    def test_window_close_after_backup_prevents_update(self):
        self.through_backup();self.now+=3*3600;self.engine.step()
        self.assertEqual(self.engine.job['phase'],'cancelled')
        self.assertIsNone(self.journal.read()['inflight'])
        self.assertNotIn('/core/update',[p for p,b in self.posts])

    def test_opt_out_after_backup_prevents_update(self):
        self.through_backup();self.options['allow_system_updates']=False;self.engine.step()
        self.assertNotIn('/core/update',[p for p,b in self.posts])

    def test_lost_update_reply_recovers_by_version_observation_without_replay(self):
        self.through_backup();self.fail_route='/core/update';self.engine.step()
        self.assertEqual(self.engine.job['phase'],'unknown')
        self.engine=self.new();self.engine.step();self.assertEqual(self.engine.job['phase'],'verify')
        self.engine.step();self.now+=61;self.engine.step()
        self.assertEqual([p for p,b in self.posts].count('/core/update'),1)
        self.assertEqual(self.engine.job['phase'],'healthy')
        self.assertIsNone(self.journal.read()['inflight'])

    def test_lost_backup_reply_is_never_replayed(self):
        self.fail_route='/backups/new/full';self.engine.step();self.now+=31;self.engine.step()
        self.assertEqual(self.engine.job['phase'],'unknown')
        self.engine=self.new();self.engine.step()
        self.assertEqual([p for p,b in self.posts].count('/backups/new/full'),1)

    def test_invalid_backup_stops_before_update_and_keeps_fence(self):
        self.engine.step();self.now+=31;self.engine.step();self.bad_backup=True;self.engine.step()
        self.assertEqual(self.engine.job['phase'],'failed')
        self.assertNotIn('/core/update',[p for p,b in self.posts])
        self.assertEqual(self.journal.read()['inflight']['phase'],'unknown')

    def test_available_target_change_is_not_silently_accepted(self):
        self.through_backup();self.core['version_latest']='2026.10.3';self.engine.step()
        self.assertEqual(self.engine.job['phase'],'failed')
        self.assertNotIn('/core/update',[p for p,b in self.posts])

    def test_new_revision_or_pause_prevents_dispatch(self):
        self.through_backup();self.journal.hold(expected_revision=1,paused=True);self.engine.step()
        self.assertNotIn('/core/update',[p for p,b in self.posts])

    def test_os_requires_exact_pending_version_separate_windowed_reboot_and_health(self):
        self.core['version_latest']=self.core['version'];self.through_backup();self.engine.step()
        self.assertEqual(self.engine.job['phase'],'os_ready')
        self.assertNotIn('/host/reboot',[p for p,b in self.posts])
        self.engine.step();self.assertEqual(self.engine.job['phase'],'verify')
        self.engine=self.new();self.engine.step();self.now+=61;self.engine.step()
        self.assertEqual(self.engine.job['phase'],'healthy')
        self.assertEqual([p for p,b in self.posts].count('/os/update'),1)
        self.assertEqual([p for p,b in self.posts].count('/host/reboot'),1)

    def test_os_reboot_waits_when_window_closes(self):
        self.core['version_latest']=self.core['version'];self.through_backup();self.engine.step()
        self.now+=3*3600;self.engine.step()
        self.assertEqual(self.engine.job['phase'],'unknown')
        self.assertNotIn('/host/reboot',[p for p,b in self.posts])

    def test_corrupt_job_is_preserved_and_not_replaced(self):
        self.engine.path.write_text('{bad')
        with self.assertRaises(WindowError): self.new()
        self.assertEqual(self.engine.path.read_text(),'{bad');self.assertEqual(self.posts,[])

    def test_second_process_lock_does_not_dispatch(self):
        import fcntl,os
        fd=os.open(self.engine.path.with_suffix('.lock'),os.O_RDWR|os.O_CREAT,0o600)
        self.addCleanup(os.close,fd);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.engine.step();self.assertIsNone(self.engine.job);self.assertEqual(self.posts,[])


if __name__=='__main__': unittest.main()
