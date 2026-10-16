import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).parents[1] / "homekind_bridge"
sys.path.insert(0, str(ROOT))
from radio_directory import RadioDirectory, station_choice, MAX_STATIONS
from media_control import MediaControls, playback

spec = importlib.util.spec_from_file_location('radio_bridge_test', ROOT / 'bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def station(name, number=1, **extra):
    return {'title': name, 'can_play': True, 'media_content_id': 'media-source://radio_browser/00000000-0000-4000-8000-' + str(number).zfill(12), 'media_content_type': 'audio/mpeg', **extra}


class RadioTests(unittest.TestCase):
    def connector(self, request=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        c = bridge.Connector({'entity_ids': ['media_player.sonos'], 'media_control_entity_ids': ['media_player.sonos']}, directory.name, request=request or (lambda *a: {'ok': True}))
        c.ha_token = 'local-test-only'
        c.credentials.update(bridge_id='synthetic', token='cloud-test-only')
        c.stream_connected = True
        c.current['media_player.sonos'] = {'entity_id': 'media_player.sonos', 'state': 'playing', 'attributes': {'supported_features': 1 | 4 | 512 | 16384, 'media_title': 'A song', 'volume_level': .3}}
        return c

    def test_station_accepts_only_radio_browser_ids_and_audio_types(self):
        valid = station_choice(station('ABC Jazz'))
        self.assertRegex(valid['key'], r'^[a-f0-9]{64}$')
        self.assertEqual(valid['label'], 'ABC Jazz')
        for changes in [{'media_content_id': 'https://private.invalid/audio'}, {'media_content_id': 'media-source://camera/private'}, {'media_content_id': 'media-source://radio_browser/not-a-uuid'}, {'media_content_type': 'text/html'}, {'title': 'file://private/music'}, {'can_play': False}, {'title': ''}]:
            self.assertIsNone(station_choice(station('ABC Jazz', **changes)))

    def test_catalogue_prioritises_three_different_presets_and_keeps_ids_local(self):
        c = self.connector(); c.ha = lambda path: {'components': ['radio_browser'], 'country': 'AU'}
        radio = RadioDirectory(c, ['media_player.sonos'])
        radio.browse_media_source = lambda country: [station('Zulu', 8), station('ABC Jazz', 3), station('ABC Classic AAC', 4), station('ABC Classic', 2), station('ABC Radio Melbourne', 1), station('https://private.invalid', 9)]
        radio.refresh(); result = radio.catalogue()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual([s['label'] for s in result['stations'][:3]], ['ABC Radio Melbourne', 'ABC Classic', 'ABC Jazz'])
        self.assertNotIn('media-source', json.dumps(result))
        self.assertNotIn('private.invalid', json.dumps(result))
        key = result['stations'][0]['key']
        self.assertTrue(radio.resolve(key)['media_content_id'].startswith('media-source://radio_browser/'))
        self.assertIsNone(radio.resolve('unknown'))

    def test_catalogue_is_bounded_and_bad_country_never_becomes_a_media_path(self):
        c = self.connector(); c.ha = lambda path: {'components': ['radio_browser'], 'country': '../private'}
        radio = RadioDirectory(c, ['media_player.sonos']); countries = []
        radio.browse_media_source = lambda country: countries.append(country) or [station('Radio '+str(i), i+1) for i in range(100)]
        radio.refresh(); self.assertEqual(countries, ['AU']); self.assertEqual(len(radio.catalogue()['stations']), MAX_STATIONS)
        with self.assertRaises(ValueError): radio.browse_media_source = RadioDirectory.browse_media_source.__get__(radio); radio.browse_media_source('../private', connect=lambda *a, **k: self.fail('must not connect'))

    def test_missing_setup_and_opt_out_do_not_browse_or_offer_station_targets(self):
        c = self.connector(); c.ha = lambda path: {'components': [], 'country': 'AU'}
        radio = RadioDirectory(c, ['media_player.sonos']); radio.browse_media_source = lambda country: self.fail('must not browse without setup')
        radio.refresh(); self.assertEqual(radio.catalogue()['status'], 'setup'); self.assertIsNone(radio.resolve('a'*64))
        c.allowlist = set(); radio.refresh(); self.assertEqual(radio.catalogue()['status'], 'no-player')

    def test_websocket_authentication_and_browse_use_only_the_local_home_assistant(self):
        c = self.connector(); c.supervisor = True
        radio = RadioDirectory(c, ['media_player.sonos']); sent = []; args = []
        class Socket:
            def __init__(self): self.answers = iter([{'type': 'auth_required'}, {'type': 'auth_ok'}, {'id': 1, 'type': 'result', 'success': True, 'result': {'children': [station('ABC Jazz')]}}])
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def recv(self, **kwargs): return json.dumps(next(self.answers))
            def send(self, value): sent.append(json.loads(value))
        def connect(url, **kwargs): args.append((url, kwargs)); return Socket()
        nodes = radio.browse_media_source('AU', connect=connect)
        self.assertEqual(nodes[0]['title'], 'ABC Jazz')
        self.assertEqual(args[0][0], 'ws://supervisor/core/websocket')
        self.assertIsNone(args[0][1]['proxy'])
        self.assertEqual(sent[0], {'type': 'auth', 'access_token': c.ha_token})
        self.assertEqual(sent[1], {'id': 1, 'type': 'media_source/browse_media', 'media_content_id': 'media-source://radio_browser/country/AU'})

    def test_radio_playback_requires_cloud_authorisation_and_a_locally_known_key(self):
        calls = []; key = station_choice(station('ABC Jazz'))['key']
        def request(url, token, data=None):
            calls.append((url, data))
            if url.endswith('media-authorize'): return {'authorised': True, 'entity_id': 'media_player.sonos', 'action': 'radio', 'value': key}
            if '/states/' in url: return {'entity_id': 'media_player.sonos', 'state': 'playing', 'attributes': {'supported_features': 512}}
            return {'ok': True}
        c = self.connector(request); worker = MediaControls(c)
        worker.radio.stations = {key: station_choice(station('ABC Jazz'))}; worker.radio.status = 'ready'; worker.browsed_at['media_player.sonos'] = 10**12
        worker.execute({'id': 'synthetic', 'lease_id': 'synthetic'})
        play = [(url, data) for url, data in calls if url.endswith('services/media_player/play_media')]
        self.assertEqual(len(play), 1); self.assertEqual(play[0][1]['entity_id'], 'media_player.sonos')
        self.assertTrue(play[0][1]['media_content_id'].startswith('media-source://radio_browser/'))
        cloud = [data for url, data in calls if '/media-' in url]
        self.assertNotIn('media-source://', json.dumps(cloud))
        self.assertTrue(c.read_only)
        for allowed, unknown in [(False, key), (True, 'unknown')]:
            calls.clear()
            def rejected(url, token, data=None):
                calls.append((url, data)); return {'authorised': allowed, 'entity_id': 'media_player.sonos', 'action': 'radio', 'value': unknown}
            c.request = rejected; worker.execute({'id': 'synthetic', 'lease_id': 'synthetic'})
            self.assertFalse(any(url.endswith('services/media_player/play_media') for url, data in calls))

    def test_media_source_addresses_in_title_fields_are_not_uploaded(self):
        state = self.connector().current['media_player.sonos']
        state['attributes']['media_title'] = station('ABC Jazz')['media_content_id']
        self.assertEqual(playback(state, {})['title'], '')


if __name__ == '__main__':
    unittest.main()
