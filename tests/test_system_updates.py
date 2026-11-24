from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'bridge'))
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

    def new(self): return SystemUpdater(self.c,self.journal,lambda:self.authority,lambda component,version: True)

    def call(self,url,token,payload=None,timeout=25):
        if url=='http://supervisor/core/api/': return {'message':'API running.'}
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

    def test_restart_before_reservation_discards_only_the_undispatched_job(self):
        with patch.object(self.journal, 'reserve', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                self.engine.step()
        previous = self.new().job['id']
        self.assertIsNone(self.journal.read()['inflight'])

        self.engine = self.new()
        self.engine.step()
        self.assertEqual(self.engine.job['phase'], 'cancelled')
        self.engine.step()
        self.assertNotEqual(self.engine.job['id'], previous)
        self.assertEqual(self.journal.read()['inflight']['operation_id'], self.engine.job['id'])
        self.assertEqual(self.posts, [])

    def test_restart_after_reservation_retains_its_saved_job(self):
        reserve = self.journal.reserve

        def interrupted(*args, **kwargs):
            reserve(*args, **kwargs)
            raise SystemExit

        with patch.object(self.journal, 'reserve', side_effect=interrupted):
            with self.assertRaises(SystemExit):
                self.engine.step()

        self.engine = self.new()
        self.assertEqual(self.journal.read()['inflight']['operation_id'], self.engine.job['id'])
        self.now += 31
        self.engine.step()
        self.engine.step()
        self.assertEqual([p for p, _ in self.posts], ['/backups/new/full'])

    def test_restart_closes_verified_receipt_without_replaying_update(self):
        self.through_backup()
        for _ in range(3):
            self.engine.step()
        self.now += 61
        with patch.object(self.journal, 'finish', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                self.engine.step()

        self.engine = self.new()
        self.assertEqual(self.engine.job['phase'], 'healthy')
        self.engine.step()
        self.assertIsNone(self.journal.read()['inflight'])
        self.assertEqual(self.new().job['phase'], 'healthy')
        self.assertEqual([p for p, _ in self.posts].count('/core/update'), 1)

    def test_restart_closes_cancelled_receipt_without_dispatch(self):
        self.engine.step()
        self.now += 31
        self.options['allow_system_updates'] = False
        with patch.object(self.journal, 'cancel_before_update', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                self.engine.step()

        self.engine = self.new()
        self.assertEqual(self.engine.job['phase'], 'cancelled')
        self.engine.step()
        self.assertIsNone(self.journal.read()['inflight'])
        self.assertEqual(self.posts, [])

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

    def test_local_opt_out_or_schedule_change_during_readiness_prevents_update(self):
        self.through_backup()
        ready = self.engine.ready
        changes = [
            lambda: self.options.update(allow_system_updates=False),
            lambda: self.journal.hold(expected_revision=1, paused=False),
        ]
        for change in changes:
            with self.subTest(change=change):
                def change_on_ready():
                    if ready_check.call_count == 2:
                        change()
                    return ready()

                with patch.object(self.engine, 'ready', side_effect=change_on_ready) as ready_check:
                    self.engine.step()
                self.assertEqual(self.engine.job['phase'], 'backup_done')
                self.assertNotIn('/core/update', [p for p, _ in self.posts])
                self.options['allow_system_updates'] = True

    def test_recheck_local_scope_after_saving_the_dispatch_fence(self):
        self.through_backup()
        save = self.engine.save

        def change_pairing():
            save()
            if self.engine.job['phase'] == 'update_dispatch':
                self.c.data['credential']['token'] = 'b' * 64

        with patch.object(self.engine, 'save', side_effect=change_pairing):
            self.engine.step()

        self.assertEqual(self.new().job['phase'], 'backup_done')
        self.assertNotIn('/core/update', [p for p, _ in self.posts])

    def test_schedule_change_at_the_backup_dispatch_fence_blocks_the_backup(self):
        self.engine.step()
        self.now += 31
        save = self.engine.save

        def change_schedule():
            save()
            if self.engine.job['phase'] == 'backup_dispatch':
                self.journal.hold(expected_revision=1, paused=False)

        with patch.object(self.engine, 'save', side_effect=change_schedule):
            self.engine.step()

        self.assertEqual(self.new().job['phase'], 'preparing')
        self.assertEqual(self.posts, [])

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

    def test_os_opt_out_during_pre_reboot_backup_check_blocks_reboot(self):
        self.core['version_latest'] = self.core['version']
        self.through_backup()
        self.engine.step()
        self.assertEqual(self.engine.job['phase'], 'os_ready')
        backup = self.engine.backup

        def opt_out():
            backup()
            self.options['allow_system_updates'] = False

        with patch.object(self.engine, 'backup', side_effect=opt_out):
            self.engine.step()

        self.assertEqual(self.new().job['phase'], 'os_ready')
        self.assertNotIn('/host/reboot', [p for p, _ in self.posts])

    def test_pending_os_proof_after_lost_reply_allows_only_the_distinct_reboot(self):
        self.core['version_latest'] = self.core['version']
        self.through_backup()
        self.fail_route = '/os/update'
        self.engine.step()
        self.assertEqual(self.engine.job['phase'], 'unknown')

        self.engine = self.new()
        self.engine.step()
        self.assertEqual(self.engine.job['phase'], 'os_ready')
        self.engine.step()
        routes = [p for p, _ in self.posts]
        self.assertEqual(routes.count('/os/update'), 1)
        self.assertEqual(routes.count('/host/reboot'), 1)

    def test_corrupt_job_is_preserved_and_not_replaced(self):
        self.engine.path.write_text('{bad')
        with self.assertRaises(WindowError): self.new()
        self.assertEqual(self.engine.path.read_text(),'{bad');self.assertEqual(self.posts,[])

    def test_second_process_lock_does_not_dispatch(self):
        import fcntl,os
        fd=os.open(self.engine.path.with_suffix('.lock'),os.O_RDWR|os.O_CREAT,0o600)
        self.addCleanup(os.close,fd);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.engine.step();self.assertIsNone(self.engine.job);self.assertEqual(self.posts,[])


    def test_no_release_approval_never_takes_a_backup_or_updates(self):
        self.engine.approved=lambda component,version:False
        self.engine.step()
        self.assertIsNone(self.engine.job)
        self.assertEqual(self.posts,[])

    def test_approval_revoked_after_backup_prevents_installation(self):
        self.through_backup();self.engine.approved=lambda component,version:False
        self.engine.step()
        self.assertEqual(self.engine.job['phase'],'cancelled')
        self.assertNotIn('/core/update',[p for p,b in self.posts])

    def test_beta_or_older_versions_cannot_be_scheduled(self):
        self.os['version_latest']=self.os['version']
        for version in ['2026.11.0b1','2026.10.0','https://other.invalid']:
            self.core['version_latest']=version;self.engine.step()
            self.assertIsNone(self.engine.job)
        self.assertEqual(self.posts,[])

    def test_recovery_key_is_retained_separately_after_the_next_operation(self):
        self.through_backup()
        saved=self.c.directory/'system-backups'/(self.engine.job['id']+'.json')
        self.assertEqual(json.loads(saved.read_text())['password'],self.engine.job['password'])
        self.assertEqual(saved.stat().st_mode & 0o777,0o600)

    def test_slow_valid_configuration_check_completes_and_rechecks_window(self):
        self.through_backup()
        original=self.c.supervisor
        def supervisor(path,payload=None,timeout=25):
            if path=='/core/check':
                if timeout < 40:
                    raise TimeoutError('Synthetic slow validation')
                self.now+=40
                return {}
            return original(path,payload,timeout)
        self.c.supervisor=supervisor
        self.engine.step()
        self.assertEqual(self.engine.job['phase'],'update')
        self.assertEqual([p for p,b in self.posts].count('/core/update'),1)

    def test_window_closing_during_configuration_check_prevents_installation(self):
        self.through_backup();original=self.c.supervisor
        def supervisor(path,payload=None,timeout=25):
            if path=='/core/check':
                self.assertEqual(timeout,300);self.now+=3*3600;return {}
            return original(path,payload,timeout)
        self.c.supervisor=supervisor;self.engine.step()
        self.assertNotIn('/core/update',[p for p,b in self.posts])

    def test_target_changing_during_configuration_check_prevents_installation(self):
        self.through_backup()
        original = self.c.supervisor

        def supervisor(path, payload=None, timeout=25):
            if path == '/core/check':
                self.core['version_latest'] = '2026.10.3'
                return {}
            return original(path, payload, timeout)

        self.c.supervisor = supervisor
        self.engine.step()

        self.assertEqual(self.engine.job['phase'], 'failed')
        self.assertNotIn('/core/update', [path for path, _ in self.posts])

    def test_revoked_approval_during_configuration_check_prevents_installation(self):
        self.through_backup();original=self.c.supervisor
        def supervisor(path,payload=None,timeout=25):
            if path=='/core/check':
                self.engine.approved=lambda component,version:False;return {}
            return original(path,payload,timeout)
        self.c.supervisor=supervisor;self.engine.step()
        self.assertNotIn('/core/update',[p for p,b in self.posts])

    def test_configuration_timeout_is_logged_privately_and_never_replayed(self):
        import io
        from contextlib import redirect_stdout
        self.through_backup();original=self.c.supervisor
        def supervisor(path,payload=None,timeout=25):
            if path=='/core/check':
                self.assertEqual(timeout,300)
                try: raise TimeoutError('private configuration detail')
                except TimeoutError as error: raise MaintenanceError('private response') from error
            return original(path,payload,timeout)
        self.c.supervisor=supervisor;output=io.StringIO()
        with redirect_stdout(output): self.engine.step()
        self.assertEqual(output.getvalue(),'System configuration check: timed out; update held.\n')
        self.assertEqual(self.engine.job['phase'],'failed')
        before=self.engine.path.read_bytes();self.authority=False;self.engine=self.new();self.engine.step()
        self.assertEqual(self.engine.path.read_bytes(),before)
        self.assertNotIn('/core/update',[p for p,b in self.posts])

    def test_invalid_configuration_stays_held_with_encrypted_backup_retained(self):
        import io
        from contextlib import redirect_stdout
        self.through_backup();original=self.c.supervisor
        def supervisor(path,payload=None,timeout=25):
            if path=='/core/check': raise MaintenanceError('private configuration error')
            return original(path,payload,timeout)
        self.c.supervisor=supervisor;output=io.StringIO()
        with redirect_stdout(output): self.engine.step()
        self.assertIn('failed or unverified; update held.',output.getvalue())
        self.assertNotIn('private',output.getvalue())
        self.assertEqual(self.engine.job['phase'],'failed')
        self.assertEqual(self.engine.job['backup_id'],'abcd1234')
        self.assertTrue((self.c.directory/'system-backups'/(self.engine.job['id']+'.json')).exists())
        self.assertNotIn('/core/update',[p for p,b in self.posts])

    def test_readiness_reports_actual_local_checks_without_exporting_private_values(self):
        original=self.c.supervisor
        for change,reason in [({'healthy':False},'supervisor_unhealthy'),({'supported':False},'supervisor_unsupported'),({'channel':'beta'},'non_stable_channel')]:
            def supervisor(path,payload=None,timeout=2):
                result=original(path,payload,timeout)
                return {**result,**change} if path=='/supervisor/info' else result
            self.c.supervisor=supervisor
            self.assertEqual(self.engine.readiness(),reason);self.assertFalse(self.engine.ready())
        self.c.supervisor=original;self.idle=False
        self.assertEqual(self.engine.readiness(),'readiness_unavailable');self.assertFalse(self.engine.ready())
        self.idle=True;self.assertEqual(self.engine.readiness(),'ready')
        self.c.call=lambda *a,**k: (_ for _ in ()).throw(MaintenanceError('private response never exported'))
        self.assertEqual(self.engine.readiness(),'readiness_unavailable');self.assertFalse(self.engine.ready())
        self.assertEqual(self.posts,[])


    def test_pre_dispatch_failure_recovers_once_with_fresh_backup_next_window(self):
        self.through_backup();self.fail_route='/core/check';self.engine.step()
        failed=dict(self.engine.job);self.fail_route=None
        self.engine=self.new();self.engine.step()
        self.assertEqual(self.engine.job['phase'],'cancelled')
        self.assertEqual(json.loads((self.c.directory/'system-failures'/(failed['id']+'.json')).read_text()),failed)
        self.assertIsNone(self.journal.read()['inflight'])
        self.engine.step();self.assertIsNone(self.engine.job)
        self.now+=86400;self.through_backup();self.engine.step()
        self.assertEqual(self.engine.job['phase'],'update')
        self.assertEqual([p for p,b in self.posts].count('/backups/new/full'),2)
        self.assertEqual([p for p,b in self.posts].count('/core/update'),1)

    def test_second_configuration_failure_is_not_retried(self):
        self.through_backup();self.fail_route='/core/check';self.engine.step();self.engine.step()
        self.now+=86400;self.through_backup();self.engine.step()
        self.assertEqual(self.engine.job['phase'],'failed')
        original=self.engine.path.read_bytes()
        for _ in range(3):self.engine=self.new();self.engine.step()
        self.assertEqual(original,self.engine.path.read_bytes())

    def test_restart_after_retry_marker_reconciles_same_failure_and_spends_one_retry(self):
        self.through_backup()
        self.fail_route = '/core/check'
        self.engine.step()
        job = self.engine.job['id']
        with patch.object(self.engine, 'save', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                self.engine.step()
        self.engine = self.new()
        self.assertEqual(self.engine.job['phase'], 'failed')
        self.assertEqual(json.loads(self.engine.retry_marker().read_text())['operation_id'], job)

        self.engine.step()
        self.assertEqual(self.engine.job['phase'], 'cancelled')
        self.assertIsNone(self.journal.read()['inflight'])
        self.now += 86400
        self.through_backup()
        self.engine.step()
        self.engine.step()
        self.assertEqual(self.engine.job['phase'], 'failed')
        self.assertEqual([p for p, _ in self.posts].count('/backups/new/full'), 2)
        self.assertNotIn('/core/update', [p for p, _ in self.posts])

    def test_pre_dispatch_recovery_keeps_optout_approval_and_backup_checks(self):
        self.through_backup();self.fail_route='/core/check';self.engine.step()
        for block in ('optout','approval','pending','backup'):
            self.options['allow_system_updates']=block!='optout'
            self.engine.approved=lambda c,v: block!='approval'
            self.core['version_pending']='2026.10.2' if block=='pending' else None
            self.bad_backup=block=='backup'
            self.engine.step();self.assertEqual(self.engine.job['phase'],'failed')
        self.assertNotIn('/core/update',[p for p,b in self.posts])

    def test_dispatched_or_unknown_operations_never_enter_retry_recovery(self):
        self.through_backup()
        for stage in ('update','reboot','verify'):
            self.engine.job.update(phase='failed',stage=stage)
            self.engine.save();before=self.engine.path.read_bytes()
            self.engine.step();self.assertEqual(self.engine.path.read_bytes(),before)
        self.assertFalse((self.c.directory/'system-retries').exists())

if __name__=='__main__': unittest.main()
