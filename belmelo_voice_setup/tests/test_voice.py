"""Fictional transport and filesystem tests; no real HA or cloud service."""
import asyncio
import importlib.util
import io
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
import wave
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'voice_test_component'
pkg = types.ModuleType(PACKAGE)
pkg.__path__ = [str(ROOT / 'custom_components/belmelo_voice')]
sys.modules[PACKAGE] = pkg

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result

client = module(PACKAGE + '.client', ROOT / 'custom_components/belmelo_voice/client.py')
installer = module('voice_test_installer', ROOT / 'installer.py')

class Content:
    def __init__(self, body): self.body = body
    async def iter_chunked(self, size):
        # Chunk boundaries deliberately split JSON tokens and MP3 bytes.
        for i in range(0, len(self.body), 3): yield self.body[i:i+3]

class Response:
    def __init__(self, value, audio=False):
        self.content = Content(value if audio else json.dumps(value).encode())
        self.headers = {'Content-Type':'audio/mpeg' if audio else 'application/json'}
        self.status = 200
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False

class Session:
    def __init__(self): self.calls = []; self.fail = False
    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.fail: raise asyncio.TimeoutError()
        body = kwargs.get('json')
        if kwargs.get('data') is not None: return Response({'text':'Kitchen lights off'})
        if body['operation'] == 'speak': return Response(b'fictional-audio', audio=True)
        return Response({'text':'The lights are now off.', 'continue_conversation':False})

class VoiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = Session()
        self.voice = client.BelleClient(self.session, {'endpoint_id':'fictional-endpoint','token':'fictional-token','microphone':'kitchen-mic','name':'Kitchen'})

    async def stream(self):
        yield b'\0' * 320

    async def test_chunked_reply_and_turn_are_carried_across_engines(self):
        text = await self.voice.transcribe(self.stream())
        turn = self.session.calls[0][1]['headers']['X-Belmelo-Turn']
        value = await self.voice.converse(text, 'conversation-one', 'kitchen-mic')
        self.assertEqual(self.session.calls[1][1]['headers']['X-Belmelo-Turn'], turn)
        audio = await self.voice.speak(value['text'])
        self.assertEqual(audio, b'fictional-audio')
        await self.voice.converse('Turn them back on', 'conversation-one', 'kitchen-mic')
        self.assertEqual(self.session.calls[3][1]['json']['previous_turn_id'], turn)
        self.assertTrue(all(not kwargs['allow_redirects'] for _, kwargs in self.session.calls))

    async def test_interrupted_pipeline_allows_an_immediate_repeat(self):
        transcribed = asyncio.Event()

        async def interrupted_run():
            await self.voice.transcribe(self.stream())
            transcribed.set()
            await asyncio.Future()

        first_run = asyncio.create_task(interrupted_run())
        await transcribed.wait()
        first_run.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first_run

        text = await self.voice.transcribe(self.stream())
        await self.voice.converse(text, 'replacement', 'kitchen-mic')
        turns = [kwargs['headers']['X-Belmelo-Turn'] for _, kwargs in self.session.calls]
        self.assertNotEqual(turns[0], turns[1])
        self.assertEqual(turns[1], turns[2])

    async def test_overlapping_pipeline_tasks_keep_identical_transcripts_separate(self):
        transcribed = asyncio.Event()
        resume = asyncio.Event()

        async def first_run():
            text = await self.voice.transcribe(self.stream())
            transcribed.set()
            await resume.wait()
            await self.voice.converse(text, 'first', 'kitchen-mic')

        first = asyncio.create_task(first_run())
        await transcribed.wait()
        text = await self.voice.transcribe(self.stream())
        await self.voice.converse(text, 'second', 'kitchen-mic')
        resume.set()
        await first

        turns = [kwargs['headers']['X-Belmelo-Turn'] for _, kwargs in self.session.calls]
        self.assertNotEqual(turns[0], turns[1])
        self.assertEqual(turns[1], turns[2])
        self.assertEqual(turns[0], turns[3])

    async def test_wrong_microphone_and_unapproved_reply_never_leave_home(self):
        with self.assertRaises(client.VoiceConnectionError):
            await self.voice.converse('Lights on', 'one', 'bedroom-mic')
        with self.assertRaises(client.VoiceConnectionError):
            await self.voice.speak('Private arbitrary text')
        self.assertEqual(len(self.session.calls), 0)

    async def test_timeout_does_not_retry_a_physical_request(self):
        self.session.fail = True
        with self.assertRaises(client.VoiceConnectionError):
            await self.voice.converse('Lights on', 'one', 'kitchen-mic')
        self.assertEqual(len(self.session.calls), 1)

    async def test_audio_is_bounded_and_framed(self):
        framed = client.make_wave(b'\0' * 320)
        with wave.open(io.BytesIO(framed)) as reader:
            self.assertEqual((reader.getnchannels(), reader.getsampwidth(), reader.getframerate()), (1, 2, 16000))
        with self.assertRaises(client.VoiceConnectionError): client.make_wave(b'\0' * 960002)
        with self.assertRaises(client.VoiceConnectionError): client.make_wave(b'\0')
        with patch.object(client, 'MAX_REPLY', 10):
            with self.assertRaises(client.VoiceConnectionError):
                await client._request(self.session, data={'operation':'speak'})

class InstallTests(unittest.TestCase):
    def test_preparation_only_adds_pilot_when_explicitly_chosen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'config';config.mkdir()
            installer.prepare(source=ROOT/'custom_components/belmelo_voice',models=ROOT/'wake_words',config=config,data=root/'data')
            self.assertFalse((config/'custom_wake_words').exists())
            installer.prepare(pilot=True,source=ROOT/'custom_components/belmelo_voice',models=ROOT/'wake_words',config=config,data=root/'data')
            self.assertEqual(len(list((config/'custom_wake_words').iterdir())),2)
            self.assertEqual(json.loads((config/'custom_components/belmelo_voice/manifest.json').read_text())['version'],'0.3.2')
    def test_preparation_failure_rolls_back_only_new_pilot_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'config';config.mkdir();target=config/'custom_wake_words';target.mkdir()
            (target/'local_model.json').write_text('local')
            with patch.object(installer,'install',side_effect=OSError('fictional disk failure')):
                with self.assertRaises(OSError):installer.prepare(pilot=True,models=ROOT/'wake_words',config=config,data=root/'data')
            self.assertEqual([p.name for p in target.iterdir()],['local_model.json'])
    def test_actual_packaged_pilot_integrity_and_gate_are_preserved(self):
        release=json.loads((ROOT/'wake_words/release.json').read_text())
        self.assertEqual(release['status'],'experimental')
        self.assertFalse(release['physical_acceptance'])
        self.assertFalse(release['original_strict_gate_passed'])
        self.assertEqual([m['id'] for m in release['models']],['belmelo_hey_belle_pilot_v1'])
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)
            self.assertEqual(installer.install_models(ROOT/'wake_words',config),2)
            self.assertEqual(installer.install_models(ROOT/'wake_words',config),0)

    def pilot_package(self, root):
        source=root/'models';source.mkdir()
        release={'status':'experimental','physical_acceptance':False,'models':[]}
        for word_id, phrase in installer.MODEL_IDS.items():
            manifest={'type':'micro','version':2,'wake_word':phrase,'model':word_id+'.tflite'}
            files={word_id+'.json':json.dumps(manifest).encode(),word_id+'.tflite':b'\x00'*4+b'TFL3'+b'\x00'*120}
            for name,content in files.items():(source/name).write_bytes(content)
            release['models'].append({'id':word_id,'files':{n:hashlib.sha256(b).hexdigest() for n,b in files.items()}})
        (source/'release.json').write_text(json.dumps(release))
        return source

    def test_pilot_models_are_idempotent_and_preserve_local_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.pilot_package(root);config=root/'config';config.mkdir()
            (config/'configuration.yaml').write_text('local choices')
            target=config/'custom_wake_words';target.mkdir();(target/'another_model.json').write_text('local model')
            self.assertEqual(installer.install_models(source,config),4)
            self.assertEqual(installer.install_models(source,config),0)
            self.assertEqual((config/'configuration.yaml').read_text(),'local choices')
            self.assertEqual((target/'another_model.json').read_text(),'local model')

    def test_pilot_integrity_failure_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.pilot_package(root);config=root/'config';config.mkdir()
            (source/'belmelo_hey_belle_pilot_v1.tflite').write_bytes(b'corrupt')
            with self.assertRaises(ValueError):installer.install_models(source,config)
            self.assertFalse((config/'custom_wake_words').exists())

    def test_pilot_id_collision_and_symlink_preserve_existing_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.pilot_package(root);config=root/'config';config.mkdir()
            target=config/'custom_wake_words';target.mkdir();local=target/'belmelo_hey_belle_pilot_v1.json';local.write_text('local custom model')
            with self.assertRaises(ValueError):installer.install_models(source,config)
            self.assertEqual(list(target.iterdir()),[local])
            self.assertEqual(local.read_text(),'local custom model')
            local.unlink();target.rmdir();target.symlink_to(source,target_is_directory=True)
            with self.assertRaises(ValueError):installer.install_models(source,config)

    def test_pilot_partial_write_is_rolled_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.pilot_package(root);config=root/'config';config.mkdir()
            original=Path.hardlink_to
            count=0
            def fail_second(path,other):
                nonlocal count
                count+=1
                if count==2:raise OSError('fictional disk failure')
                return original(path,other)
            with patch.object(Path,'hardlink_to',fail_second):
                with self.assertRaises(OSError):installer.install_models(source,config)
            self.assertEqual(list((config/'custom_wake_words').iterdir()),[])

    def test_install_upgrade_preserves_configuration_and_backs_up_only_own_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config'; config.mkdir(); (config/'configuration.yaml').write_text('unchanged')
            data=Path(tmp)/'data'
            installer.install(ROOT/'custom_components/belmelo_voice', config, data)
            target=config/'custom_components/belmelo_voice'
            (target/'previous.txt').write_text('old integration')
            installer.install(ROOT/'custom_components/belmelo_voice', config, data)
            self.assertEqual((config/'configuration.yaml').read_text(),'unchanged')
            self.assertFalse((target/'previous.txt').exists())
            self.assertEqual(len(list(data.glob('belmelo_voice_backup_*'))),1)

    def test_symlink_or_foreign_integration_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config';config.mkdir();outside=Path(tmp)/'outside';outside.mkdir()
            (config/'custom_components').symlink_to(outside)
            with self.assertRaises(ValueError): installer.install(ROOT/'custom_components/belmelo_voice',config,Path(tmp)/'data')
            self.assertEqual(list(outside.iterdir()),[])

if __name__ == '__main__': unittest.main()
