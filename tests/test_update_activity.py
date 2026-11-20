"""Render the real HA template and exercise the actual updater and window runtime."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

from template_contract import activity_response

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homekind_maintenance.system_updates import SystemUpdater, IDLE_TEMPLATE
from homekind_maintenance.window_runtime import WindowRuntime
from homekind_maintenance.update_diagnostics import read_observation
from homekind_maintenance.recovery_ui import update_check_details


class UpdateActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 11, 19, 15, 5, tzinfo=timezone.utc).timestamp()
        self.players = []; self.scripts = []; self.posts = []; self.reports = []; self.local_reads = []; self.states = None
        self.installed = '2026.10.4'; self.approved = True; self.extra = {}
        self.options = {'allow_updates': True, 'allow_system_updates': True}
        self.snapshot = {'policy': {'schema': 1, 'revision': 7, 'enabled': True,
            'zone': 'Australia/Melbourne', 'days': list(range(7)), 'start': '02:00', 'end': '05:00'},
            'paused': False, 'defer_until': None}
        self.c = SimpleNamespace(directory=Path(self.temp.name), target='a123bc45_homekind_bridge',
            data={'credential': {'id': '74000000-0000-4000-8000-000000000001', 'token': 'a' * 64}},
            options=lambda: self.options, clock=lambda: self.now, token='synthetic-local',
            supervisor=self.supervisor, call=self.call)
        self.runtime = WindowRuntime(self.c)

    def player(self, source, name='Bedroom speaker'):
        self.players.append(SimpleNamespace(state='playing', name=name,
            entity_id='media_player.bedroom', attributes={'media_content_id': source}))

    def supervisor(self, path, payload=None, timeout=2):
        if payload is not None:
            self.posts.append(path)
            if path == '/backups/new/full':
                self.extra = payload['extra']; return {'job_id': 'b' * 32}
            if path == '/core/update':
                self.assertEqual(payload, {'version': '2026.11.2', 'backup': False, 'background': True})
                self.installed = '2026.11.2'; return {'job_id': 'c' * 32}
            if path == '/core/check': return {}
            self.fail('Unexpected Supervisor operation ' + path)
        if path == '/supervisor/info': return {'version': '2026.11.0', 'healthy': True, 'supported': True, 'channel': 'stable'}
        if path == '/core/info': return {'version': self.installed, 'version_latest': '2026.11.2', 'ip_address': '172.30.32.1'}
        if path == '/os/info': return {'version': '18.4', 'version_latest': '18.4'}
        if path == '/addons/self/info': return {'version': '0.2.6', 'version_latest': '0.2.6'}
        if path.startswith('/addons/'): return {'version': '0.25.6', 'version_latest': '0.25.6'}
        if path == '/jobs/' + 'b' * 32: return {'done': True, 'errors': [], 'child_jobs': [], 'reference': 'abcd1234'}
        if path == '/jobs/' + 'c' * 32: return {'done': True, 'errors': [], 'child_jobs': []}
        if path == '/backups/abcd1234/info': return {'slug': 'abcd1234', 'type': 'full', 'protected': True,
            'homeassistant': '2026.10.4', 'homeassistant_exclude_database': False,
            'extra': self.extra, 'locations': ['.local'], 'location_attributes': {'.local': {'protected': True}}}
        self.fail('Unexpected Supervisor read ' + path)

    def call(self, url, token, body=None, timeout=2):
        self.local_reads.append(url)
        if url == 'http://supervisor/core/api/': return {'message': 'API running.'}
        if url == 'http://supervisor/core/api/template':
            return activity_response(body['template'], self.states or SimpleNamespace(media_player=self.players, script=self.scripts))
        self.assertTrue(url.endswith('/maintenance-windows'))
        self.reports.append(json.loads(json.dumps(body['report'])))
        return {'protocol': 1, 'authority_current': True, 'executor_ready': body['report']['executor_ready'],
            'desired': self.snapshot, 'approved': {'core': '2026.11.2'} if self.approved else {}}

    def assert_audio_window_completes(self, source):
        self.player(source)
        engine = SystemUpdater(self.c, None)
        self.assertEqual(engine.readiness(), 'ready')
        for _ in range(12):
            self.runtime.step(); self.now += 31
        self.assertEqual(self.installed, '2026.11.2')
        self.assertEqual(self.posts.count('/backups/new/full'), 1)
        self.assertEqual(self.posts.count('/core/update'), 1)
        self.assertTrue(any(r.get('execution', {}).get('phase') == 'healthy' for r in self.reports))
        self.assertNotIn('http://supervisor/core/api/config', self.local_reads)
        self.assertNotIn('Bedroom speaker', json.dumps(self.reports))
        self.assertNotIn('private', json.dumps(self.reports))

    def test_independent_overnight_audio_allows_the_approved_window_to_complete(self):
        self.assert_audio_window_completes('https://radio.example.org/live.mp3?private=never-export')
        self.assertNotIn('radio.example.org', json.dumps(self.reports))
        self.assertNotIn('never-export', json.dumps(self.reports))

    def test_home_assistant_audio_allows_backup_installation_and_healthy_completion(self):
        self.assert_audio_window_completes('http://homeassistant.local:8123/media/local/private-sleep.mp3')

    def test_unknown_audio_allows_backup_installation_and_healthy_completion(self):
        self.assert_audio_window_completes('')

    def test_running_script_is_an_explicit_blocker_even_with_independent_audio(self):
        self.player('spotify:track:synthetic')
        self.scripts = [SimpleNamespace(state='on', name='Evening routine', entity_id='script.evening')]
        self.assertEqual(SystemUpdater(self.c, None).readiness(), 'script_running')
        self.runtime.step(); self.assertEqual(self.posts, [])
        self.assertEqual(self.reports[-1]['readiness'], 'script_running')

    def test_no_audio_source_or_metadata_can_veto_readiness(self):
        for source in ['https://my-home.example.net/api/tts_proxy/secret.mp3',
                       'http://homeassistant.local:8123/media/local/sleep.mp3',
                       'media-source://media_source/local/private.mp3', '', 'unknown-source',
                       'http://192.168.1.8/music.mp3', 'http://127.0.0.1/music.mp3',
                       'http://[::1]/music.mp3', 'https://my-home.example.net/sound.mp3',
                       'https://public.example.org/%61pi/tts_proxy/audio.mp3',
                       'https://public.example.org:8123/sound', 'https://user:secret@public.example.org/live',
                       'file:///data/secret', 'javascript:alert(1)', 'x' * 4097, None, {'invalid': True}]:
            self.players = []; self.player(source)
            with self.subTest(source=str(source)[:80]):
                engine = SystemUpdater(self.c, None)
                self.assertEqual(engine.readiness(), 'ready')
                self.assertEqual(engine.activity, {'scripts': [], 'players': [], 'background_audio': 0})
        self.approved = False; self.runtime.step(); self.assertEqual(self.posts, [])
        self.assertNotIn('secret.mp3', json.dumps(self.reports))

    def test_audio_does_not_override_release_window_or_local_opt_out(self):
        self.player('media-source://media_source/local/private.mp3')
        self.approved = False; self.runtime.step(); self.assertEqual(self.posts, [])
        self.approved = True; self.now += 12 * 3600; self.runtime.step(); self.assertEqual(self.posts, [])
        self.options['allow_system_updates'] = False; self.runtime.step(); self.assertEqual(self.posts, [])

    def test_local_diagnostics_identify_the_script_and_keep_private_media_data_local(self):
        self.player('https://radio.example.org/private-stream?token=never-export')
        self.scripts = [SimpleNamespace(state='on', name='Night routine <private>', entity_id='script.night')]
        self.runtime.step()
        observation = read_observation(self.c.directory)
        self.assertEqual(observation['reason'], 'script_running')
        self.assertEqual(observation['blockers'], ['Night routine <private>'])
        self.assertNotIn('private-stream', json.dumps(observation))
        page = update_check_details(self.c.directory)
        self.assertIn('Night routine &lt;private&gt;', page)
        self.assertNotIn('Night routine <private>', page)
        self.assertIn('Audio playback does not delay updates.', page)
        self.assertNotIn('Night routine', json.dumps(self.reports))
        self.scripts = []; self.now += 31; self.approved = False; self.runtime.step()
        observation = read_observation(self.c.directory)
        self.assertEqual(observation['reason'], 'approval_needed')
        self.assertEqual(observation['history'][0]['reason'], 'script_running')
        self.assertEqual(observation['history'][-1]['reason'], 'approval_needed')
        self.assertEqual(self.posts, [])

    def test_actual_template_never_reads_a_media_player(self):
        class ScriptStates:
            script = []

            @property
            def media_player(self):
                raise AssertionError('Media metadata must not be read for maintenance.')

        self.states = ScriptStates()
        self.assertEqual(SystemUpdater(self.c, None).readiness(), 'ready')
        self.assertEqual(activity_response(IDLE_TEMPLATE, self.states), {'scripts': []})

    def test_unverifiable_script_checks_still_hold_without_dispatch(self):
        for value in [False, None, {}, {'scripts': 'unknown'},
                      {'scripts': [{'entity_id': 'media_player.speaker', 'name': 'Speaker'}]},
                      {'scripts': [{'entity_id': 'script.night', 'name': 'invalid\nlabel'}]}]:
            original = self.c.call
            def call(url, token, body=None, timeout=2):
                if url == 'http://supervisor/core/api/template': return value
                return original(url, token, body, timeout)
            self.c.call = call
            with self.subTest(value=value):
                self.assertEqual(SystemUpdater(self.c, None).readiness(), 'readiness_unavailable')
                self.runtime.step(); self.assertEqual(self.posts, [])
            self.c.call = original

    def test_script_bounds_remain_a_hold_and_diagnostics_are_limited(self):
        self.scripts = [SimpleNamespace(state='on', name='Routine ' + str(i), entity_id='script.night_' + str(i)) for i in range(65)]
        self.assertEqual(SystemUpdater(self.c, None).readiness(), 'readiness_unavailable')
        self.runtime.step(); self.assertEqual(self.posts, [])
        self.scripts.pop(); self.now += 31; self.runtime.step()
        observation = read_observation(self.c.directory)
        self.assertEqual(observation['reason'], 'script_running')
        self.assertEqual(len(observation['blockers']), 16)
        self.assertEqual(self.posts, [])

    def test_corrupt_optional_diagnostics_never_modify_the_saved_policy_or_allow_a_start(self):
        self.approved = False; self.runtime.step()
        before = self.runtime.journal.path.read_bytes()
        (self.c.directory / 'update-check.json').write_text('{corrupt')
        self.runtime.step()
        self.assertEqual(self.runtime.journal.path.read_bytes(), before)
        self.assertEqual(self.posts, [])
        self.assertEqual(read_observation(self.c.directory)['reason'], 'approval_needed')


if __name__ == '__main__': unittest.main()
