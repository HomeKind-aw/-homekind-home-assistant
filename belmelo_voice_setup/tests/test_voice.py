"""Fictional transport and filesystem tests; no real HA or cloud service."""
import asyncio
import importlib.util
import io
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
