import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

APP = Path(__file__).parents[1] / "homekind_bridge"
sys.path.insert(0, str(APP))
import health_import as health
import bridge

NOW = dt.datetime(2026, 10, 17, 0, tzinfo=dt.timezone.utc).timestamp()

def report(**changes):
    return {'content':'date,Steps,Sleep,Resting HR,Private\n2026-10-16,8432,7:30,54,UNRELATED_SECRET\n2026-10-17,1200,420,55,UNRELATED_SECRET\n','format':'csv','mapping':{'date':'date','steps':'Steps','sleep':'Sleep','resting_hr':'Resting HR'},'date_format':'iso','sleep_unit':'minutes','timezone':'Australia/Melbourne','label':'Alun Garmin','source':'garmin_export',**changes}

class NativeHealthTests(unittest.TestCase):
    def manager(self, directory, clock=lambda:NOW):
        self.published=[]
        return health.HealthImports(directory,lambda entity,state:self.published.append((entity,state)),bridge.atomic_json,clock=clock)

    def test_preview_is_local_minimal_and_latest_per_metric(self):
        with tempfile.TemporaryDirectory() as d:
            m=self.manager(d);p=m.preview(report())
            self.assertEqual(self.published,[]);self.assertFalse(m.file.exists())
            self.assertEqual({r['metric']:r['value'] for r in p['readings']},{'steps':1200,'sleep':420,'resting_hr':55})
            self.assertNotIn('UNRELATED_SECRET',str(m.previews));self.assertNotIn('content',str(m.previews));self.assertNotIn('value',str(m.status()));self.assertFalse(m.status()['automatic_sync'])

    def test_confirmation_single_use_preview_and_private_file(self):
        with tempfile.TemporaryDirectory() as d:
            m=self.manager(d);p=m.preview(report())
            with self.assertRaises(health.HealthImportError):m.commit({'preview_id':p['preview_id']})
            self.assertFalse(m.file.exists());m.commit({'preview_id':p['preview_id'],'publish_to_ha':True})
            self.assertEqual(len(self.published),3);self.assertEqual(os.stat(m.file).st_mode&0o777,0o600)
            for entity,state in self.published:
                self.assertRegex(entity,r'^sensor\.homekind_health_[0-9a-f]{12}_(steps|sleep|resting_hr)$');self.assertEqual(state['attributes']['homekind_health_date'],'2026-10-17')
            self.assertNotIn('UNRELATED_SECRET',m.file.read_text())
            with self.assertRaises(health.HealthImportError):m.commit({'preview_id':p['preview_id'],'publish_to_ha':True})

    def test_date_format_units_and_thousands_are_explicit(self):
        data=report(content='Day,Steps,Sleep,Resting HR\n16/10/2026,"8,432",7.5,54\n',mapping={'date':'Day','steps':'Steps','sleep':'Sleep','resting_hr':'Resting HR'},date_format='dmy',sleep_unit='hours')
        readings,_,_=health.parse_report(data,NOW)
        self.assertEqual({r['metric']:r['value'] for r in readings},{'steps':8432,'sleep':450,'resting_hr':54})
        with self.assertRaises(health.HealthImportError):health.parse_report({**data,'date_format':'mdy'},NOW)

    def test_invalid_future_conflicting_formula_and_extreme_values_are_rejected(self):
        for content in ['date,Steps\n2026-10-18,100\n','date,Steps\n2026-10-17,100\n2026-10-17,200\n','date,Steps\n2026-10-17,=1+1\n','date,Steps\n2026-10-17,1.5\n','date,Steps\n2026-10-17,Infinity\n','date,Steps\n2026-10-17,999999999\n']:
            with self.subTest(content=content),self.assertRaises(health.HealthImportError):health.parse_report(report(content=content,mapping={'date':'date','steps':'Steps'}),NOW)

    def test_old_rows_are_excluded_and_json_does_not_invent_measurements(self):
        readings,skipped,_=health.parse_report(report(content=json.dumps([{'date':'2026-10-01','steps':8000},{'date':'2026-10-17','steps':1234,'gps':'UNRELATED_SECRET'}]),format='json'),NOW)
        self.assertEqual(skipped,1);self.assertEqual(readings,[{'metric':'steps','date':'2026-10-17','value':1234,'unit':'steps'}])
        for item in [{'steps':5000},{'date':'2026-10-17','steps':True},{'date':'2026-10-17','steps':float('nan')}]:
            with self.assertRaises(health.HealthImportError):health.parse_report(report(content=json.dumps([item]),format='json'),NOW)

    def test_oversized_deep_and_unsupported_files_are_bounded(self):
        for content,kind in [('x'*131073,'csv'),('PK secret archive','zip'),('[['*2000,'json'),('date,Steps,Steps\n2026-10-17,1,2','csv'),('date,Steps\n2026-10-17,1,extra','csv')]:
            with self.subTest(kind=kind),self.assertRaises(health.HealthImportError):health.read_report(content,kind)

    def test_stale_previews_and_older_imports_cannot_overwrite_newer_data(self):
        clock=[NOW]
        with tempfile.TemporaryDirectory() as d:
            m=self.manager(d,clock=lambda:clock[0]);a=m.preview(report());b=m.preview(report());m.commit({'preview_id':a['preview_id'],'publish_to_ha':True})
            with self.assertRaises(health.HealthImportError):m.commit({'preview_id':b['preview_id'],'publish_to_ha':True})
            profile=next(iter(m.store['profiles']))
            with self.assertRaises(health.HealthImportError):m.preview(report(profile_id=profile,content='date,Steps,Sleep,Resting HR\n2026-10-16,123,400,54'))
            p=m.preview(report(profile_id=profile));clock[0]+=601
            with self.assertRaises(health.HealthImportError):m.commit({'preview_id':p['preview_id'],'publish_to_ha':True})
            m.refresh();self.assertEqual(m.previews,{})

    def test_existing_source_person_provider_and_timezone_cannot_change(self):
        with tempfile.TemporaryDirectory() as d:
            m=self.manager(d);p=m.preview(report());m.commit({'preview_id':p['preview_id'],'publish_to_ha':True});profile=next(iter(m.store['profiles']))
            for change in [{'label':'Other person'},{'source':'health_file'},{'timezone':'UTC'},{'profile_id':[]}]:
                data=report(profile_id=profile);data.update(change)
                with self.assertRaises(health.HealthImportError):m.preview(data)

    def test_expiry_clears_values_instead_of_refreshing_the_source_date(self):
        clock=[NOW]
        with tempfile.TemporaryDirectory() as d:
            m=self.manager(d,clock=lambda:clock[0]);p=m.preview(report());m.commit({'preview_id':p['preview_id'],'publish_to_ha':True});clock[0]+=7*86400;m.refresh()
            self.assertTrue(all(not p['readings'] for p in m.store['profiles'].values()));self.assertTrue(all(s['state']=='unavailable' for _,s in self.published[-3:]));self.assertNotIn('1200',m.file.read_text());self.assertNotIn('420',m.file.read_text())

    def test_removal_cleanup_survives_ha_outage_and_restart(self):
        with tempfile.TemporaryDirectory() as d:
            m=self.manager(d);p=m.preview(report());m.commit({'preview_id':p['preview_id'],'publish_to_ha':True});profile=next(iter(m.store['profiles']))
            def offline(*args):raise OSError('sensitive-provider-error')
            m.publish=offline;m.remove({'profile_id':profile})
            self.assertNotIn('sensitive-provider-error',m.message);self.assertEqual(m.store['profiles'],{});self.assertEqual(len(m.store['removed']),3)
            restored=self.manager(d);restored.refresh();self.assertEqual(len(self.published),3);self.assertEqual(restored.store['removed'],{})
            self.assertEqual({s['attributes']['device_class'] for _,s in self.published},{'health_steps','health_sleep','health_resting_hr'})

    def test_existing_personal_consent_gates_health_upload_and_keeps_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            m=self.manager(d);p=m.preview(report());m.commit({'preview_id':p['preview_id'],'publish_to_ha':True});entity,value=self.published[0]
            stamp=dt.datetime.fromtimestamp(NOW,dt.timezone.utc).isoformat();s={'entity_id':entity,**value,'last_updated':stamp,'last_changed':stamp}
            c=bridge.Connector({'entity_ids':[entity]},Path(d)/'connector',clock=lambda:NOW);c.tracked=[{'entity_id':entity,'kind':'health','collect_health':False}]
            c.accept_state(s);self.assertEqual(c.pending,{});c.tracked[0]['collect_health']=True;c.accept_state(s)
            r=c.pending[entity];self.assertEqual(r['source_date'],'2026-10-17');self.assertEqual(r['import_method'],'file');self.assertEqual(r['source'],'garmin_export');self.assertNotIn('UNRELATED_SECRET',str(r))
            s['attributes']['homekind_health_date']='2026-99-99';self.assertEqual(bridge.observation(s)['state'],'unavailable')

if __name__=='__main__':unittest.main()
