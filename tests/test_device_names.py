import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
sys.path.insert(0, str(Path(__file__).parents[1] / 'homekind_bridge'))
from bridge import atomic_json
from device_names import DeviceNames, checked, label_ok
from device_onboarding import z2m_route
IEEE='0x54ef4410015f8319'
ENTITY='binary_sensor.'+IEEE+'_presence'
UNIQUE=IEEE+'_presence_zigbee2mqtt'
DEVICE='1'*32
BASE='my_home'

class Socket:
    def __init__(self,home): self.home,self.messages=home,[]
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def send(self,text):
        v=json.loads(text)
        if v.get('type')=='mqtt/subscribe':
            ident,topic=v['id'],v['topic']
            if topic.endswith('/bridge/devices'):
                self.messages.append({'type':'event','id':ident,'event':{'topic':topic,'retain':True,'payload':json.dumps(self.home.devices)}})
            self.messages.append({'type':'result','id':ident,'success':True,'result':None})

class Home:
    def __init__(self,test):
        self.test,self.devices=test,test.devices
        self.metadata={ENTITY:{'ha_platform':'mqtt','ha_unique_id':test.unique,'ha_device_id':DEVICE}}
        cfg={'device':{'manufacturer':'Zigbee2MQTT','model':'Bridge'},'command_topic':BASE+'/bridge/request/permit_join','state_topic':BASE+'/bridge/info','payload_off':'{"time":0}'}
        route=z2m_route({'entities':[{'discovery_data':{'payload':cfg}}]})
        self.routes={route['key']:route}
    def inspect(self):
        if self.test.local_edit: self.test.options['managed_settings']=False
    def socket(self):
        self.ws=Socket(self);self.test.ws=self.ws;return self.ws
    def auth(self,ws): pass
    def query(self,ws,ident,kind,**args):
        assert kind=='mqtt/device/debug_info' and args['device_id']==DEVICE
        return {'entities':[{'discovery_data':{'payload':{'unique_id':UNIQUE,'state_topic':BASE+'/'+IEEE}}}]}
    def receive(self,ws):
        if not ws.messages: raise TimeoutError('synthetic no reply')
        return ws.messages.pop(0)

class DeviceNameTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.now=1800000000
        self.unique,self.local_edit,self.reply=UNIQUE,False,True
        self.options={'entity_ids':[ENTITY],'managed_settings':True,'management_id':'a'*32}
        self.credentials={'bridge_id':'b','token':'synthetic-only'}
        self.devices=[{'ieee_address':IEEE,'friendly_name':IEEE,'type':'EndDevice'}];self.calls=[]
        self.manager=SimpleNamespace(directory=Path(self.temp.name),lock=threading.RLock(),options=lambda:copy.deepcopy(self.options))
        self.manager.credentials=self.manager.directory/'credentials.json';atomic_json(self.manager.credentials,self.credentials)
        self.manager.connector=SimpleNamespace(ha=self.publish)
        self.worker=DeviceNames(self.manager,lambda c:Home(self),clock=lambda:self.now)
    def tearDown(self): self.temp.cleanup()
    def payload(self,name='Alfie’s Presence Sensor',key='c'*64):
        return {'protocol':1,'management_id':'a'*32,'expires_at':dt.datetime.fromtimestamp(self.now+60,dt.timezone.utc).isoformat(),
                'devices':[{'key':key,'entity_id':ENTITY,'ha_unique_id':UNIQUE,'ha_device_id':DEVICE,'name':name}]}
    def publish(self,path,data):
        self.calls.append((path,copy.deepcopy(data)));command=json.loads(data['payload']);old_name=self.devices[0]['friendly_name']
        self.devices[0]['friendly_name']=command['to']
        if self.reply:
            answer={'transaction':command['transaction'],'status':'ok','data':{'from':old_name,'to':command['to'],'homeassistant_rename':False}}
            event=lambda p,retain=False:{'type':'event','id':3,'event':{'topic':BASE+'/bridge/response/device/rename','retain':retain,'payload':json.dumps(p)}}
            self.ws.messages += [event({**answer,'transaction':'wrong'}),event(answer,True),event(answer)]
    def accept(self,payload=None): self.worker.accept(payload or self.payload(),copy.deepcopy(self.options),self.credentials)
    def test_exact_identity_and_no_home_assistant_entity_rename(self):
        self.accept();self.assertEqual(len(self.calls),1)
        path,data=self.calls[0];self.assertEqual(path,'services/mqtt/publish')
        self.assertEqual(data['topic'],BASE+'/bridge/request/device/rename')
        command=json.loads(data['payload']);self.assertEqual(command['from'],IEEE)
        self.assertEqual(command['to'],'Alfie’s Presence Sensor');self.assertIs(command['homeassistant_rename'],False)
        self.assertIs(data['retain'],False);self.assertEqual(self.worker.report(),[{'key':'c'*64,'status':'synced'}])
        self.assertEqual(self.options['entity_ids'],[ENTITY]);self.assertEqual(json.loads(self.manager.credentials.read_text()),self.credentials)
    def test_restart_and_local_override_do_not_replay_success(self):
        self.accept();self.devices[0]['friendly_name']='Local choice'
        self.worker=DeviceNames(self.manager,lambda c:Home(self),clock=lambda:self.now);self.accept()
        self.assertEqual(len(self.calls),1);self.assertEqual(self.devices[0]['friendly_name'],'Local choice')
        self.accept(self.payload('New Belmelo name','d'*64));self.assertEqual(len(self.calls),2)
    def test_duplicate_name_never_renames_or_adds_silent_suffix(self):
        self.devices.append({'ieee_address':'0x0000000000000001','friendly_name':'Alfie’s Presence Sensor','type':'EndDevice'})
        self.accept();self.assertEqual(self.calls,[]);self.assertEqual(self.worker.report()[0]['status'],'conflict')
    def test_changed_identity_and_removed_selection_cannot_publish(self):
        self.unique='0x0000000000000000_presence_zigbee2mqtt';self.accept()
        self.assertEqual(self.calls,[]);self.assertEqual(self.worker.report()[0]['status'],'unsupported')
        self.options['entity_ids']=[];self.assertEqual(checked(self.payload(),self.options,self.now),[])
    def test_concurrent_local_opt_out_wins(self):
        self.local_edit=True;self.accept();self.assertEqual(self.calls,[])
    def test_invalid_expired_and_arbitrary_input_cannot_publish(self):
        for change in [{'protocol':True},{'protocol':2},{'management_id':'b'*32},{'expires_at':'2020-01-01T00:00:00Z'},{'expires_at':'2027-01-15T08:01:00'},{'topic':'arbitrary/#'}]:
            self.accept({**self.payload(),**change})
        for name in ['bridge','bad/#','bad+name','bad\\name','bad\nname','x'*81]:
            self.assertFalse(label_ok(name));self.accept(self.payload(name))
        self.assertEqual(self.calls,[])
    def test_timeout_reconciles_without_blind_republish(self):
        self.reply=False;self.accept();self.assertEqual(self.worker.report()[0]['status'],'unavailable')
        self.accept();self.assertEqual(len(self.calls),1)
        self.now+=301;self.accept();self.assertEqual(len(self.calls),1);self.assertEqual(self.worker.report()[0]['status'],'synced')
    def test_matching_inventory_needs_no_publish(self):
        self.devices[0]['friendly_name']='Alfie’s Presence Sensor';self.accept()
        self.assertEqual(self.calls,[]);self.assertEqual(self.worker.report()[0]['status'],'synced')

if __name__=='__main__': unittest.main()
