import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parents[1] / 'homekind_bridge'))
from voice_events import VoiceOutbox, VoiceError
from voice_transport import VoiceCloud, VoiceCollector, VoiceWorker, VOICE_URL
from bridge import Connector, HAStream


class VoiceTransportTests(unittest.TestCase):
    def worker(self, **kwargs):
        worker=VoiceWorker(self.bridge,Path(self.temp.name)/'worker',
                           feedback=kwargs.pop('feedback',self.feedback.append),**kwargs)
        self.addCleanup(worker.stop)
        worker.start()
        return worker

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.now = dt.datetime(2026,10,18,tzinfo=dt.timezone.utc).timestamp()
        self.bridge_id,self.installation,self.endpoint,self.binding_id = [str(uuid4()) for _ in range(4)]
        self.binding = dict(protocol=1,bridge_id=self.bridge_id,installation_id=self.installation,
                            endpoint_id=self.endpoint,binding_id=self.binding_id,revision=1,
                            enabled=True,muted=False,consent_current=True,
                            expires_at=self.stamp(15),intents=['help','room_lights_on','checkin_okay'])
        self.calls=[];self.sent=0;self.status='queued';self.lost=False;self.setup_lost=False
        self.bridge = SimpleNamespace(credentials=dict(bridge_id=self.bridge_id,installation_id=self.installation,token='a'*64),clock=lambda:self.now,stopping=lambda:False,request=self.request)
        self.cloud=VoiceCloud(self.bridge)
        self.box=VoiceOutbox(self.temp.name,self.bridge_id,self.installation,clock=lambda:self.now);self.addCleanup(self.box.close)
        self.feedback=[];self.collector=VoiceCollector(self.cloud,self.box,self.feedback.append)
        self.message=dict(event_type='homekind_voice_request',time_fired=self.stamp(),context={'id':'01K7TCRZG20000000000000000'},data=dict(protocol=1,endpoint_id=self.endpoint,binding_id=self.binding_id,intent='help'))

    def stamp(self,offset=0):
        return dt.datetime.fromtimestamp(self.now+offset,dt.timezone.utc).isoformat().replace('+00:00','Z')

    def request(self,url,token,body,timeout):
        self.assertTrue(url.startswith(VOICE_URL+'/voice-'));self.assertEqual(token,'a'*64);self.assertEqual(timeout,2)
        self.calls.append((url,copy.deepcopy(body)));op=url.rsplit('-',1)[1]
        self.assertEqual(body['bridge_id'],self.bridge_id);self.assertEqual(body['installation_id'],self.installation)
        if op=='snapshot':
            if self.setup_lost:raise OSError('secret provider detail')
            return dict(protocol=1,bridge_id=self.bridge_id,installation_id=self.installation,bindings=[{**self.binding,'expires_at':self.stamp(15),'private':'never persist'}])
        if op=='dispatch':
            self.assertEqual(self.feedback[-1]['status'],'recognised')
            self.sent+=1
            self.event=body['event']
            if self.lost:raise TimeoutError('secret timeout')
        event=getattr(self,'event',None)
        return dict(protocol=1,event_id=(event or {})['event_id'],binding_id=self.binding_id,binding_revision=1,status=self.status)

    def test_actual_fixed_cloud_exchange_and_scoped_changed_feedback(self):
        ident=self.collector.submit(self.message);self.assertEqual(self.calls,[])
        self.collector.tick();self.assertEqual(self.sent,1)
        self.assertEqual([x['status'] for x in self.feedback],['recognised','queued'])
        self.assertNotIn('private',json.dumps(self.feedback));self.assertNotIn('a'*64,json.dumps(self.feedback))
        dispatch=next(b for u,b in self.calls if u.endswith('dispatch'))
        self.assertEqual(set(dispatch),{'bridge_id','installation_id','revision','event'})
        self.assertEqual(set(dispatch['event']),{'protocol','event_id','binding_id','endpoint_id','intent','occurred_at'})
        self.status='family_acknowledged';self.now+=5;self.collector.tick()
        self.assertEqual(self.feedback[-1]['event_id'],ident);self.assertEqual(self.feedback[-1]['status'],'family_acknowledged');self.assertEqual(self.sent,1)
        self.assertTrue(all('event' not in b for u,b in self.calls if u.endswith('receipt')))

    def test_authenticated_ha_session_admits_metadata_before_cloud_worker_runs(self):
        connector=Connector({'ha_url':'http://localhost:8123','entity_ids':[]},
                            Path(self.temp.name)/'local',request=self.request,clock=lambda:self.now)
        connector.ha_token='synthetic-ha-only'
        connector.ha=lambda kind:[]
        stream=HAStream(connector,connect=lambda *a:None,on_voice=self.collector.submit)
        frames=iter([{'type':'auth_required'},{'type':'auth_ok'},
            {'id':1,'type':'result','success':True},
            {'id':6,'type':'result','success':True},
            {'id':6,'type':'event','event':self.message}])
        sent=[]
        class Socket:
            def send(self,value):sent.append(json.loads(value))
            def recv(self,**kwargs):
                try:return json.dumps(next(frames))
                except StopIteration:
                    stream.stopped.set();raise TimeoutError()
        stream.session(Socket())
        self.assertEqual(sent[0],{'type':'auth','access_token':'synthetic-ha-only'})
        self.assertEqual(len(self.collector.waiting),1);self.assertEqual(self.calls,[])
        self.collector.tick();self.assertEqual(self.sent,1)
        self.assertEqual(self.feedback[-1]['status'],'queued')

    def test_real_voice_worker_processes_then_stops_and_releases_only_its_spool(self):
        delivered=threading.Event()
        worker=self.worker()
        self.assertTrue(worker.local_ready.wait(2))
        worker.collector.feedback=lambda value:(self.feedback.append(value),delivered.set() if value['status']=='queued' else None)
        worker.submit(self.message)
        self.assertTrue(delivered.wait(2));self.assertEqual(self.sent,1)
        self.assertTrue(worker.stop(2));self.assertTrue(worker.finished.is_set())
        self.assertEqual(worker.state,'stopped');self.assertFalse(worker.local_ready.is_set())
        with self.assertRaises(VoiceError):worker.submit(self.message)
        reopened=VoiceOutbox(Path(self.temp.name)/'worker',self.bridge_id,self.installation,clock=lambda:self.now)
        try:self.assertEqual(reopened.db.execute('select count(*) from events').fetchone()[0],1)
        finally:reopened.close()
        self.assertIsNotNone(self.box.db)  # Unrelated spool is still owned/open.

    def test_duplicate_worker_refuses_storage_without_stopping_the_existing_worker(self):
        first=self.worker();self.assertTrue(first.local_ready.wait(2))
        second=self.worker();self.assertTrue(second.finished.wait(2))
        self.assertEqual(second.state,'unavailable');self.assertFalse(second.local_ready.is_set())
        self.assertTrue(first.is_alive());self.assertEqual(self.calls,[])

    def test_timed_out_stop_keeps_storage_owned_until_inflight_call_really_ends(self):
        entered,release=threading.Event(),threading.Event()
        original=self.bridge.request
        def held(*args,**kwargs):
            entered.set();release.wait(2)
            return original(*args,**kwargs)
        self.bridge.request=held
        worker=self.worker();self.assertTrue(worker.local_ready.wait(2))
        worker.submit(self.message);self.assertTrue(entered.wait(2))
        self.assertFalse(worker.stop(.01));self.assertFalse(worker.finished.is_set())
        try:
            with self.assertRaises(VoiceError):VoiceOutbox(Path(self.temp.name)/'worker',self.bridge_id,self.installation,clock=lambda:self.now)
        finally:release.set()
        self.assertTrue(worker.stop(2));self.assertEqual(self.sent,0)
        self.assertTrue(worker.finished.is_set())

    def test_credential_change_ends_only_optional_worker_without_cloud_dispatch(self):
        worker=self.worker();self.assertTrue(worker.local_ready.wait(2))
        self.bridge.credentials['token']='b'*64;worker.wake.set()
        self.assertTrue(worker.finished.wait(2));self.assertEqual(worker.state,'connection_ended')
        self.assertEqual(self.calls,[]);self.assertFalse(self.bridge.stopping())

    def test_invalid_pairing_and_prestart_cancellation_never_open_or_send(self):
        self.bridge.credentials['installation_id']='invalid'
        worker=self.worker();self.assertTrue(worker.finished.wait(2))
        self.assertEqual(worker.state,'unavailable');self.assertEqual(self.calls,[])
        self.assertFalse((Path(self.temp.name)/'worker'/'voice-events.sqlite3').exists())
        stopped=VoiceWorker(self.bridge,Path(self.temp.name)/'never')
        self.assertTrue(stopped.stop());stopped.start();self.assertTrue(stopped.finished.wait(2))
        self.assertEqual(stopped.state,'stopped');self.assertFalse(stopped.directory.exists())

    def test_external_home_stop_ends_worker_without_dispatch_or_rewriting_scope(self):
        worker=self.worker();self.assertTrue(worker.local_ready.wait(2))
        self.bridge.stopping=lambda:True;worker.wake.set()
        self.assertTrue(worker.finished.wait(2));self.assertEqual(worker.state,'stopped')
        self.assertEqual(self.calls,[])

    def test_restarted_worker_recovers_receipt_without_replaying_dispatched_action(self):
        first=self.worker();self.assertTrue(first.local_ready.wait(2))
        delivered=threading.Event()
        def stop_at_dispatch_feedback(value):
            self.feedback.append(value)
            if value['status']=='queued':
                # Precisely stop between dispatch feedback and receipt lookup:
                # local cancellation must not end this accepted receipt's life.
                first.halt.set();delivered.set()
        first.collector.feedback=stop_at_dispatch_feedback
        ident=first.submit(self.message);self.assertTrue(delivered.wait(2))
        self.assertTrue(first.stop(2));self.now+=5;self.status='family_acknowledged'
        saved=VoiceOutbox(Path(self.temp.name)/'worker',self.bridge_id,self.installation,clock=lambda:self.now)
        try:self.assertEqual(saved.db.execute('select status,receipt_ended from events').fetchone(),('queued',0))
        finally:saved.close()
        received=threading.Event()
        second=self.worker(feedback=lambda value:(self.feedback.append(value),received.set()))
        self.assertTrue(second.local_ready.wait(2));self.assertTrue(received.wait(2))
        self.assertEqual(self.sent,1)
        self.assertEqual(self.feedback[-1]['event_id'],ident)
        self.assertEqual(self.feedback[-1]['status'],'family_acknowledged')

    def test_local_duplicate_and_changed_content_never_create_a_second_action(self):
        ident=self.collector.submit(self.message);self.assertEqual(self.collector.submit(self.message),ident)
        changed=copy.deepcopy(self.message);changed['data']['intent']='room_lights_on'
        with self.assertRaises(VoiceError):self.collector.submit(changed)
        self.collector.tick();self.collector.tick();self.assertEqual(self.sent,1)

    def test_event_fields_and_queue_bound_reject_untrusted_audio_or_authority(self):
        for key in ['audio','transcript','actor_id','service','recipients']:
            msg=copy.deepcopy(self.message);msg['data'][key]='never upload'
            with self.assertRaises(VoiceError):self.collector.submit(msg)
        for n in range(20):
            msg=copy.deepcopy(self.message);msg['context']['id']='01K7TCRZG2'+str(n).zfill(16);self.collector.submit(msg)
        msg=copy.deepcopy(self.message);msg['context']['id']='01K7TCRZG2'+'9'*16
        with self.assertRaises(VoiceError):self.collector.submit(msg)
        self.assertEqual(self.calls,[])

    def test_snapshot_network_failure_keeps_original_event_without_fabricating_consent(self):
        ident=self.collector.submit(self.message);self.setup_lost=True;self.collector.tick();self.assertEqual(self.sent,0)
        self.assertEqual(self.collector.waiting[0]['event_id'],ident);self.assertEqual(self.feedback,[])
        self.setup_lost=False;self.collector.tick();self.assertEqual(self.sent,1)

    def test_current_negative_consent_drops_admission_without_a_cloud_action(self):
        self.collector.submit(self.message);self.binding['muted']=True;self.collector.tick()
        self.assertEqual(self.sent,0);self.assertEqual(self.feedback,[]);self.assertEqual(len(self.collector.waiting),0)

    def test_access_and_credential_changes_fail_closed_without_secret_errors(self):
        self.collector.submit(self.message);self.bridge.credentials['token']='b'*64;self.collector.tick();self.assertEqual(self.calls,[])
        self.bridge.credentials['token']='a'*64
        self.bridge.request=lambda *a,**k:(_ for _ in ()).throw(PermissionError('private token'))
        with self.assertRaises(VoiceError) as caught:self.cloud.snapshot()
        self.assertNotIn('private',str(caught.exception))

    def test_malformed_snapshots_are_unavailable_rather_than_invented_withdrawal(self):
        good=self.request(VOICE_URL+'/voice-snapshot','a'*64,self.cloud.scope,2)
        for change in [{'protocol':True},{'installation_id':str(uuid4())},{'bindings':good['bindings']*2},{'bindings':[{**self.binding,'revision':True}]},{'bindings':[{**self.binding,'expires_at':self.stamp(-1)}]},{'bindings':[{**self.binding,'intents':['arbitrary']}]},{'bindings':[{**self.binding,'enabled':'true'}]}]:
            self.bridge.request=lambda *a,value={**good,**change},**k:value
            with self.assertRaises(OSError):self.cloud.snapshot()
        self.assertEqual(self.sent,0)

    def test_fixed_payloads_cannot_override_scope_or_supply_an_arbitrary_route(self):
        for op,payload in [('send',{}),('snapshot',{'bridge_id':str(uuid4())}),('receipt',{'service':'lock.unlock'})]:
            with self.assertRaises(VoiceError):self.cloud._post(op,payload)
        self.assertEqual(self.calls,[])

    def test_lost_send_can_recover_evidence_after_expiry_without_actuation_replay(self):
        self.collector.submit(self.message);self.lost=True;self.collector.tick();self.assertEqual(self.sent,1)
        self.now+=31;self.lost=False;self.status='family_acknowledged';self.collector.tick()
        self.assertEqual(self.sent,1);self.assertEqual(self.feedback[-1]['status'],'family_acknowledged')

    def test_overlapping_tick_cannot_duplicate_the_cloud_worker(self):
        self.collector.submit(self.message);self.collector.working.acquire()
        try:self.assertEqual(self.collector.tick(),[]);self.assertEqual(self.calls,[])
        finally:self.collector.working.release()
        self.collector.tick();self.assertEqual(self.sent,1)

    def test_muted_endpoint_never_emits_later_private_feedback(self):
        self.collector.submit(self.message);self.collector.tick();self.binding['muted']=True;self.now+=5;self.status='family_acknowledged';self.collector.tick()
        self.assertEqual([x['status'] for x in self.feedback],['recognised','queued']);self.assertEqual(self.sent,1)

    def test_optional_feedback_failure_does_not_retry_actions_or_regress_evidence(self):
        self.collector.feedback=lambda value:(_ for _ in ()).throw(OSError('speaker offline'))
        self.collector.submit(self.message)
        # Check ordering without requiring this synthetic broken output to append.
        self.feedback.append({'status':'recognised'})
        self.collector.tick();self.collector.tick();self.assertEqual(self.sent,1)


if __name__=='__main__':unittest.main()
