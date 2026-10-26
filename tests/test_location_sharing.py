import copy
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'homekind_bridge'))
from location_sharing import LocationSharing, candidates, identity, report

NOW=1793008800
ENTITY='device_tracker.alun_phone'
META={ENTITY:{'ha_unique_id':'private-local-id','ha_platform':'mobile_app','ha_device_id':'local-device'}}
SOURCE={'subject_id':'00000000-0000-4000-8000-000000000010','entity_id':ENTITY,'fingerprint':identity(ENTITY,META),'revision':1,'mode':'home_away'}
STATE={'entity_id':ENTITY,'last_updated':'2026-10-26T09:59:00Z','state':'not_home','attributes':{'source_type':'gps','gps_accuracy':15,'latitude':-37.86784,'longitude':144.83199,'friendly_name':'Alun phone','speed':4,'bearing':70}}

class LocationTests(unittest.TestCase):
    def test_discovery_has_metadata_only_and_requires_gps_and_stable_identity(self):
        rows=candidates([STATE],META)
        self.assertEqual(set(rows[0]),{'entity_id','fingerprint','name'})
        self.assertNotIn('private-local-id',str(rows))
        self.assertNotIn('latitude',str(rows))
        self.assertEqual(candidates([STATE],{}),[])
        other=copy.deepcopy(STATE);other['attributes']['source_type']='router'
        self.assertEqual(candidates([other],META),[])

    def test_home_away_payload_never_contains_coordinates_or_unselected_fields(self):
        value=report(STATE,SOURCE,META,NOW)
        self.assertIsNotNone(value)
        self.assertEqual(value['state'],'away')
        for key in ['latitude','longitude','speed','bearing','attributes','name']:
            self.assertNotIn(key,value)

    def test_approximate_coordinates_are_rounded_locally(self):
        value=report(STATE,{**SOURCE,'mode':'approximate'},META,NOW)
        self.assertEqual(value['latitude'],-37.868)
        self.assertEqual(value['longitude'],144.832)
        self.assertEqual(value['accuracy'],15)

    def test_unknown_bad_accuracy_and_nan_do_not_become_away(self):
        for key,value in [('gps_accuracy',1000),('gps_accuracy',None),('latitude',float('nan')),('longitude',float('inf')),('latitude',True)]:
            state=copy.deepcopy(STATE);state['attributes'][key]=value
            answer=report(state,{**SOURCE,'mode':'approximate'},META,NOW)
            self.assertEqual(answer['state'],'unknown')
            self.assertNotIn('latitude',answer)

    def test_missing_future_and_naive_source_times_never_receive_a_fresh_timestamp(self):
        for value in [None,'2026-11-01T10:00:00Z','2026-10-26T10:00:00','invalid']:
            state={**STATE,'last_updated':value}
            self.assertIsNone(report(state,SOURCE,META,NOW))

    def test_changed_entity_identity_stops_collection(self):
        self.assertIsNone(report(STATE,{**SOURCE,'fingerprint':'a'*64},META,NOW))
        self.assertIsNone(report({**STATE,'entity_id':'device_tracker.other'},SOURCE,META,NOW))

    def worker(self):
        self.calls=[];self.options={'managed_settings':True,'management_id':'a'*32,'entity_ids':['sensor.existing']}
        client=SimpleNamespace(stream_connected=True,ha=lambda path:self.calls.append(path) or ([STATE] if path=='states' else STATE))
        manager=SimpleNamespace(connector=client,options=lambda:copy.deepcopy(self.options))
        class Home:
            def __init__(self,c):self.c=c;self.metadata=META
            def inspect(self):pass
        return LocationSharing(manager,clock=lambda:NOW,home_factory=Home)

    def response(self,sources=None,discover=False):
        return {'protocol':1,'management_id':'a'*32,'discover':discover,'sources':sources or []}

    def test_disabled_location_adds_no_home_queries_or_polls(self):
        worker=self.worker();worker.accept(self.response(),self.options)
        self.assertEqual(worker.prepare(self.options)['reports'],[])
        self.assertEqual(self.calls,[])

    def test_explicit_source_can_only_read_its_known_local_entity(self):
        worker=self.worker();worker.accept(self.response([SOURCE]),self.options)
        result=worker.prepare(self.options)
        self.assertEqual(self.calls,['states/'+ENTITY])
        self.assertEqual(len(result['reports']),1)
        self.assertEqual(self.options['entity_ids'],['sensor.existing'])

    def test_local_opt_out_replacement_and_unsupported_directives_fail_closed(self):
        for change in [{'managed_settings':False},{'management_id':'b'*32}]:
            worker=self.worker();worker.accept(self.response([SOURCE]),self.options)
            options={**self.options,**change};self.options=options
            self.assertEqual(worker.prepare(options)['reports'],[])
            self.assertEqual(self.calls,[])
        worker=self.worker();worker.accept(self.response([{**SOURCE,'entity_id':'http://untrusted'}]),self.options)
        self.assertEqual(worker.prepare(self.options)['reports'],[])

    def test_cached_server_directive_expires_in_45_seconds(self):
        worker=self.worker();worker.accept(self.response([SOURCE]),self.options)
        worker.clock=lambda:NOW+46
        self.assertEqual(worker.prepare(self.options)['reports'],[])
        self.assertEqual(self.calls,[])

    def test_concurrent_local_opt_out_discards_an_already_collected_report(self):
        worker=self.worker();worker.accept(self.response([SOURCE]),self.options)
        options=copy.deepcopy(self.options)
        def read(path):
            self.options['managed_settings']=False
            return STATE
        worker.manager.connector.ha=read
        self.assertEqual(worker.prepare(options)['reports'],[])

    def test_lost_configuration_reply_clears_pending_location_collection(self):
        worker=self.worker();worker.accept(self.response([SOURCE]),self.options)
        worker.accept(None,self.options)
        self.assertEqual(worker.prepare(self.options)['reports'],[])
        self.assertEqual(self.calls,[])

if __name__=='__main__':unittest.main()
