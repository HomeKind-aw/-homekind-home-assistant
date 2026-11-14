"""Run inside the pinned Home Assistant image, with no live home or provider."""
import asyncio
import importlib
import inspect
from pathlib import Path
import sys
import types
from unittest.mock import AsyncMock, Mock, patch
from homeassistant.components import assist_pipeline, stt, tts
from homeassistant.components.conversation import ConversationEntity, ConversationEntityFeature

root=Path(__file__).resolve().parents[1]/'custom_components/belmelo_voice'
pkg=types.ModuleType('belle_core_contract');pkg.__path__=[str(root)];sys.modules[pkg.__name__]=pkg
speech=importlib.import_module(pkg.__name__+'.stt')
conversation=importlib.import_module(pkg.__name__+'.conversation')
spoken=importlib.import_module(pkg.__name__+'.tts')
importlib.import_module(pkg.__name__+'.config_flow')

class Client:
    endpoint='fictional';name='Fictional kitchen'
    async def transcribe(self, stream): return 'Kitchen lights off'
    async def converse(self,*args): return {'text':'A fictional reply','continue_conversation':False}
    async def speak(self,text): return b'fictional-mp3'

async def main():
    a=speech.BelleSTT(Client());b=conversation.BelleConversation(Client());c=spoken.BelleTTS(Client())
    assert isinstance(a,stt.SpeechToTextEntity) and not inspect.isabstract(type(a))
    assert isinstance(b,ConversationEntity) and b.supported_features & ConversationEntityFeature.CONTROL
    assert isinstance(c,tts.TextToSpeechEntity) and not inspect.isabstract(type(c))
    metadata=stt.SpeechMetadata(language='en',format=stt.AudioFormats.WAV,codec=stt.AudioCodecs.PCM,
        bit_rate=stt.AudioBitRates.BITRATE_16,sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,channel=stt.AudioChannels.CHANNEL_MONO)
    assert a.check_metadata(metadata)
    assert (await a.async_process_audio_stream(metadata,None)).text=='Kitchen lights off'
    result=await b._async_handle_message(types.SimpleNamespace(text='Kitchen lights off',conversation_id='fictional',language='en',device_id='fictional'),None)
    assert result.response.as_dict()['speech']['plain']['speech']=='A fictional reply'
    assert (await c.async_get_tts_audio('A fictional reply','en'))[0]=='mp3'
    assert 'prefer_local_intents' in inspect.signature(assist_pipeline.async_update_pipeline).parameters
    assert 'pipeline_name' in inspect.signature(assist_pipeline.async_create_default_pipeline).parameters
    # Exercise the real integration setup against the real core API surface,
    # with a fictional registry: never choose a different microphone or bypass
    # the scoped conversation engine through local intents.
    spec=importlib.util.spec_from_file_location('belle_setup_contract',root/'__init__.py',submodule_search_locations=[str(root)])
    setup=importlib.util.module_from_spec(spec);sys.modules[spec.name]=setup;spec.loader.exec_module(setup)
    pipeline=types.SimpleNamespace(id='own-pipeline',name='Belle · Kitchen')
    entry=types.SimpleNamespace(entry_id='fictional-entry',title='Kitchen',data={'endpoint_id':'fictional','token':'fictional-token','name':'Kitchen','microphone':'kitchen-mic','pipeline_id':'own-pipeline'})
    registry=types.SimpleNamespace(async_get_entity_id=lambda domain,*args:domain+'.belle')
    hass=types.SimpleNamespace(data={},config_entries=types.SimpleNamespace(async_forward_entry_setups=AsyncMock(),async_update_entry=Mock()),
        states=types.SimpleNamespace(get=lambda entity:types.SimpleNamespace(attributes={'options':[pipeline.name]})),services=types.SimpleNamespace(async_call=AsyncMock()))
    with patch.object(setup,'async_get_clientsession',return_value=object()),patch.object(setup.er,'async_get',return_value=registry),\
        patch.object(setup.er,'async_entries_for_device',return_value=[types.SimpleNamespace(domain='select',entity_id='select.kitchen_assistant')]) as lookup,\
        patch.object(setup.assist_pipeline,'async_get_pipelines',return_value=[pipeline]),patch.object(setup.assist_pipeline,'async_update_pipeline',new_callable=AsyncMock) as update:
        assert await setup.async_setup_entry(hass,entry)
        assert lookup.call_args.args[1]=='kitchen-mic'
        assert update.call_args.kwargs['prefer_local_intents'] is False
        assert update.call_args.kwargs['conversation_engine']=='conversation.belle'
        assert hass.services.async_call.call_args.args[2]=={'entity_id':'select.kitchen_assistant','option':'Belle · Kitchen'}
    print('PASS real Home Assistant 2026.11.1 engine and pipeline API contract; no physical acceptance')

asyncio.run(main())
