"""Fictional satellite events exercise the actual commissioning coordinator."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

root=Path(__file__).resolve().parents[1]/'custom_components/belmelo_voice'
pkg=types.ModuleType('satellite_tests');pkg.__path__=[str(root)];sys.modules[pkg.__name__]=pkg
stubs={n:types.ModuleType(n) for n in ['homeassistant','homeassistant.components','homeassistant.components.assist_pipeline','homeassistant.components.persistent_notification','homeassistant.helpers','homeassistant.helpers.entity_registry','homeassistant.helpers.event']}
stubs['homeassistant.helpers.event'].async_track_time_interval=Mock()
with patch.dict(sys.modules,stubs):
    spec=importlib.util.spec_from_file_location('satellite_tests.satellite',root/'satellite.py');sat=importlib.util.module_from_spec(spec);sys.modules[spec.name]=sat;spec.loader.exec_module(sat)

@dataclass
class Word:
    id:str
    wake_word:str
@dataclass
class Config:
    available_wake_words:list
    active_wake_words:list
    max_active_wake_words:int=1

class SatelliteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config=Config([Word('jarvis','Hey Jarvis'),Word('nabu','Okay Nabu')],['nabu'])
        self.satellite=types.SimpleNamespace(available=True,async_get_configuration=lambda:self.config,
            async_set_configuration=AsyncMock(),async_intercept_wake_word=AsyncMock(return_value='Hey Jarvis'))
        self.registry=types.SimpleNamespace(async_get_entity_id=lambda kind,*args:kind+'.belle')
        self.pipeline=types.SimpleNamespace(id='own',name='Belle · Kitchen',prefer_local_intents=False,stt_engine='stt.belle',conversation_engine='conversation.belle',tts_engine='tts.belle')
        self.selected=types.SimpleNamespace(state=self.pipeline.name,attributes={'options':[self.pipeline.name]})
        self.hass=types.SimpleNamespace(data={'assist_satellite':types.SimpleNamespace(get_entity=lambda entity:self.satellite if entity=='assist_satellite.kitchen' else None)},states=types.SimpleNamespace(get=lambda entity:self.selected),services=types.SimpleNamespace(async_call=AsyncMock()))
        self.entry=types.SimpleNamespace(entry_id='paired-kitchen',title='Kitchen',data={'pipeline_id':'own'})
        self.hass.config_entries=types.SimpleNamespace(async_update_entry=Mock(
            side_effect=lambda entry, *, data: setattr(entry, 'data', data)))
        self.hass.async_add_executor_job=AsyncMock(return_value=False)
        self.client=types.SimpleNamespace(endpoint='paired-endpoint',microphone='paired-kitchen',sync=AsyncMock(return_value={'test':None}))
        self.connection=sat.SatelliteConnection(self.hass,self.entry,self.client)
        self.entries=[types.SimpleNamespace(domain='assist_satellite',disabled_by=None,entity_id='assist_satellite.kitchen'),types.SimpleNamespace(domain='select',disabled_by=None,entity_id='select.kitchen_assistant')]
        self.patches=[patch.object(sat.er,'async_get',return_value=self.registry,create=True),patch.object(sat.er,'async_entries_for_device',return_value=self.entries,create=True),patch.object(sat.assist_pipeline,'async_get_pipelines',return_value=[self.pipeline],create=True)]
        self.patches.extend(patch.object(sat.persistent_notification, name, create=True)
                            for name in ('async_create', 'async_dismiss'))
        for p in self.patches:p.start()
    async def asyncTearDown(self):
        await self.connection.close()
        for p in self.patches:p.stop()
    async def test_only_bound_device_and_installed_model_can_be_selected(self):
        self.assertFalse(await self.connection.select_word('hey_belle'))
        self.satellite.async_set_configuration.assert_not_awaited()
        self.assertTrue(await self.connection.select_word('jarvis'))
        self.assertEqual(self.satellite.async_set_configuration.call_args.args[0].active_wake_words,['jarvis'])
        self.assertEqual(sat.er.async_entries_for_device.call_args.args[1],'paired-kitchen')

    async def test_prepare_waits_for_advertised_model_and_confirms_once(self):
        self.hass.async_add_executor_job.return_value=True
        await self.connection.poll()
        self.satellite.async_set_configuration.assert_not_awaited()
        self.config.available_wake_words.append(Word(sat.HEY_BELLE, 'Hey Belle'))
        await self.connection.poll()
        self.assertEqual(self.satellite.async_set_configuration.await_args.args[0].active_wake_words,[sat.HEY_BELLE])
        await self.connection.poll()
        self.assertNotIn(sat.WAKE_SETUP_FINISHED,self.entry.data)
        self.satellite.async_set_configuration.assert_awaited_once()
        self.config.active_wake_words=[sat.HEY_BELLE]
        await self.connection.poll()
        self.assertTrue(self.entry.data[sat.WAKE_SETUP_FINISHED])
        self.assertEqual(self.client.sync.await_args.args[0]['active_wake_word'],sat.HEY_BELLE)
        await self.connection.close()
        self.connection=sat.SatelliteConnection(self.hass,self.entry,self.client)
        self.config.active_wake_words=['jarvis']
        await self.connection.poll()
        self.satellite.async_set_configuration.assert_awaited_once()

    async def test_auto_wake_requires_prepare_and_an_enabled_ready_pipeline(self):
        self.config.available_wake_words.append(Word(sat.HEY_BELLE, 'Hey Belle'))
        await self.connection.poll()
        self.satellite.async_set_configuration.assert_not_awaited()
        self.connection.auto_wake_enabled=True
        self.selected.state='Home Assistant'
        await self.connection.poll()
        self.selected.state=self.pipeline.name
        self.client.sync.return_value={'enabled':False}
        await self.connection.poll()
        self.client.sync.return_value={'test':{'id':'test','mode':'single_phrase'}}
        await self.connection.poll()
        self.satellite.async_set_configuration.assert_not_awaited()

    async def test_advanced_choice_supersedes_automatic_setup(self):
        self.connection.auto_wake_enabled=True
        self.config.available_wake_words.append(Word(sat.HEY_BELLE, 'Hey Belle'))
        self.client.sync.return_value={'wake_word':'jarvis'}
        await self.connection.poll()
        self.client.sync.return_value={}
        await self.connection.poll()
        self.satellite.async_set_configuration.assert_awaited_once()
        self.assertEqual(self.satellite.async_set_configuration.await_args.args[0].active_wake_words,['jarvis'])

    async def test_unsupported_model_produces_one_actionable_notice(self):
        self.connection.auto_wake_enabled=True
        for _ in range(8):
            await self.connection.poll()
        self.satellite.async_set_configuration.assert_not_awaited()
        sat.persistent_notification.async_create.assert_called_once()
        self.assertIn('firmware',sat.persistent_notification.async_create.call_args.args[1])
    async def test_runtime_checks_all_engines_and_current_local_selection(self):
        self.assertTrue(self.connection.runtime()['pipeline_ready'])
        self.selected.state='Home Assistant'
        self.assertFalse(self.connection.runtime()['pipeline_ready'])
        self.selected.state=self.pipeline.name;self.pipeline.prefer_local_intents=True
        self.assertFalse(self.connection.runtime()['pipeline_ready'])
    async def test_test_reports_actual_intercepted_wake_without_passing_itself(self):
        self.client.sync.return_value={'test':{'id':'test','status':'wake'}}
        await self.connection.watch({'id':'test'})
        events=[c.args[1] for c in self.client.sync.call_args_list if c.args[1]]
        self.assertEqual([e['type'] for e in events],['armed','wake'])
        self.assertEqual(events[-1]['wake_word'],'Hey Jarvis')
        self.assertFalse(any(e['type']=='passed' for e in events))
    async def test_wrong_pipeline_never_arms_a_wake_intercept(self):
        self.selected.state='Home Assistant'
        await self.connection.watch({'id':'test'})
        self.satellite.async_intercept_wake_word.assert_not_awaited()
        self.assertEqual(self.client.sync.call_args.args[1]['error'],'pipeline_mismatch')
    async def test_timeout_cannot_pass(self):
        self.satellite.async_intercept_wake_word=AsyncMock(side_effect=asyncio.TimeoutError())
        await self.connection.watch({'id':'test'})
        self.assertEqual(self.client.sync.call_args.args[1]['type'],'failed')
    async def test_unload_cancels_interception_and_poll_timer(self):
        self.satellite.async_intercept_wake_word=AsyncMock(side_effect=lambda:None)
        event=asyncio.Event()
        self.connection.watcher=asyncio.create_task(event.wait())
        self.connection.cancel_interval=Mock()
        await asyncio.sleep(0)
        await self.connection.close()
        self.assertTrue(self.connection.watcher.cancelled())
        self.connection.cancel_interval.assert_called_once()
    def request(self, seconds=60):
        return {'id':str(uuid4()),'expires_at':(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()}
    async def test_connect_is_explicit_bounded_and_targets_only_bound_microphone(self):
        self.selected.state='Home Assistant'
        request=self.request()
        await self.connection.connect(request)
        self.hass.services.async_call.assert_awaited_once_with('select','select_option',{'entity_id':'select.kitchen_assistant','option':'Belle · Kitchen'},blocking=True)
        await self.connection.connect(request)
        self.assertEqual(self.hass.services.async_call.await_count,1)
    async def test_invalid_expired_foreign_engine_and_ambiguous_connect_do_nothing(self):
        for request in [self.request(-1),self.request(100),{**self.request(),'service':'arbitrary'},self.request()|{'id':'not-a-uuid'}]:
            self.assertFalse(await self.connection.connect(request))
        self.pipeline.conversation_engine='conversation.someone_else'
        self.assertFalse(await self.connection.connect(self.request()))
        self.pipeline.conversation_engine='conversation.belle'
        self.entries.append(types.SimpleNamespace(domain='select',disabled_by=None,entity_id='select.another'))
        self.assertFalse(await self.connection.connect(self.request()))
        self.hass.services.async_call.assert_not_awaited()
    async def test_normal_poll_preserves_local_assistant_and_single_phrase_is_not_intercepted(self):
        self.selected.state='Home Assistant'
        await self.connection.poll()
        self.hass.services.async_call.assert_not_awaited()
        self.client.sync.return_value={'test':{'id':'single','status':'command','mode':'single_phrase'}}
        await self.connection.poll()
        self.satellite.async_intercept_wake_word.assert_not_awaited()
    async def test_unload_while_status_is_in_flight_cannot_connect_or_change_local_phrase(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def slow_sync(*args):
            started.set()
            await release.wait()
            return {'connection_request':self.request(), 'wake_word':'jarvis'}
        self.client.sync.side_effect=slow_sync
        pending=asyncio.create_task(self.connection.poll())
        await started.wait()
        await self.connection.close()
        release.set()
        await pending
        self.hass.services.async_call.assert_not_awaited()
        self.satellite.async_set_configuration.assert_not_awaited()
        self.assertFalse(await self.connection.connect(self.request()))
