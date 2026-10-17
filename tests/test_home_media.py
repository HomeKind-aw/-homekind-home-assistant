import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest
import sys

ROOT = Path(__file__).parents[1] / "homekind_bridge"
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('media_control', ROOT / 'media_control.py')
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)
bridge_spec = importlib.util.spec_from_file_location('bridge_media_test', ROOT / 'bridge.py')
bridge = importlib.util.module_from_spec(bridge_spec)
bridge_spec.loader.exec_module(bridge)


class MediaTests(unittest.TestCase):
    def state(self):
        return {'entity_id': 'media_player.sonos', 'state': 'playing', 'attributes': {'supported_features': 1 | 4 | 512 | 16384 | 131072, 'media_title': 'A song', 'media_artist': 'Artist', 'volume_level': .3, 'entity_picture': 'http://private/art', 'media_content_id': 'http://private/music'}}

    def connector(self, request):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        c = bridge.Connector({'entity_ids': ['media_player.sonos'], 'media_control_entity_ids': ['media_player.sonos']}, directory.name, request=request)
        c.ha_token = 'local'; c.credentials.update(bridge_id='test', token='cloud')
        c.current['media_player.sonos'] = self.state(); c.stream_connected = True
        return c

    def test_sync_never_waits_for_favourite_browsing(self):
        calls=[]
        c=self.connector(lambda url,token,data=None:calls.append(url) or {'ok':True})
        worker=media.MediaControls(c)
        worker.browse=lambda *args:self.fail('Favourite discovery blocked playback')
        worker.sync()
        self.assertTrue(any(url.endswith('media-sync') for url in calls))

    def test_slow_media_poll_does_not_hold_up_playback_publishing(self):
        entered,release,published=[threading.Event() for _ in range(3)]
        def request(url,token,data=None):
            if url.endswith('media-poll'):
                entered.set();release.wait(3);return {'commands':[]}
            if url.endswith('media-sync') and entered.is_set():published.set()
            return {'ok':True}
        c=self.connector(request);c.ready.set()
        worker=media.MediaControls(c);worker.radio.start=lambda:None
        worker.radio.catalogue=lambda:{'status':'ready','country':'AU','stations':[]}
        worker.browse=lambda *args:None
        c.media_worker=worker;worker.start()
        try:
            self.assertTrue(entered.wait(1));worker.last_payload=None;worker.wake.set()
            self.assertTrue(published.wait(1))
        finally:
            c.halt.set();release.set();worker.join(timeout=3)

    def test_home_assistant_media_change_wakes_only_the_media_lane(self):
        c=self.connector(lambda *args:{})
        worker=media.MediaControls(c);c.media_worker=worker
        state=self.state();state.update(last_updated='2026-10-17T00:00:01Z',last_changed='2026-10-17T00:00:01Z')
        c.accept_state(state);self.assertTrue(worker.wake.is_set())

    def test_now_playing_has_only_supported_fields_and_no_content_addresses(self):
        result = media.playback(self.state(), {})
        self.assertEqual(result['volume'], .3)
        self.assertEqual(result['title'], 'A song')
        self.assertNotIn('private', str(result))
        for value in [float('nan'), float('inf'), True, -1, 1.1]:
            state = self.state(); state['attributes']['volume_level'] = value
            self.assertNotIn('volume', media.playback(state, {}))

    def test_idle_does_not_share_the_previous_track(self):
        state = self.state(); state['state'] = 'idle'
        self.assertNotIn('title', media.playback(state, {}))

    def test_browsed_content_ids_stay_local_and_only_opaque_keys_leave(self):
        def request(url, token, data=None):
            return {'service_response': {'media_player.sonos': {'children': [{'title': 'My radio', 'can_play': True, 'media_content_type': 'music', 'media_content_id': 'http://private/radio'}]}}}
        worker = media.MediaControls(self.connector(request)); worker.browse('media_player.sonos', self.state())
        self.assertEqual(len(worker.choices['media_player.sonos']), 1)
        snapshot = media.playback(self.state(), worker.choices['media_player.sonos'])
        self.assertNotIn('http', str(snapshot)); self.assertEqual(len(snapshot['choices'][0]['key']), 64)

    def test_only_the_locally_selected_entity_and_explicit_service_can_be_controlled(self):
        calls = []
        def request(url, token, data=None):
            calls.append((url, data))
            if url.endswith('media-authorize'): return {'authorised': True, 'entity_id': 'media_player.other', 'action': 'pause', 'value': None}
            return {'ok': True}
        worker = media.MediaControls(self.connector(request)); worker.execute({'id': 'x', 'lease_id': 'y'})
        self.assertFalse(any('services/' in x[0] for x in calls))

    def test_playback_acknowledges_a_fresh_device_read_without_changing_light_mode(self):
        calls = []
        def request(url, token, data=None):
            calls.append((url, data))
            if url.endswith('media-authorize'): return {'authorised': True, 'entity_id': 'media_player.sonos', 'action': 'pause', 'value': None}
            if '/states/' in url:
                state = self.state(); state['state'] = 'paused'; return state
            return {'ok': True}
        c = self.connector(request); worker = media.MediaControls(c); worker.browsed_at['media_player.sonos'] = 10**12
        worker.execute({'id': 'x', 'lease_id': 'y'})
        self.assertTrue(c.read_only)
        self.assertTrue(any(url.endswith('services/media_player/media_pause') for url, _ in calls))
        self.assertEqual(next(data for url, data in calls if url.endswith('media-ack'))['status'], 'confirmed')

    def test_unknown_or_out_of_range_actions_never_call_home_assistant(self):
        for action, value in [('turn_off', None), ('volume', 2), ('volume', True), ('favourite', 'unknown')]:
            calls = []
            def request(url, token, data=None):
                calls.append(url)
                if url.endswith('media-authorize'): return {'authorised': True, 'entity_id': 'media_player.sonos', 'action': action, 'value': value}
                return {'ok': True}
            worker = media.MediaControls(self.connector(request)); worker.browsed_at['media_player.sonos'] = 10**12
            worker.execute({'id': 'x', 'lease_id': 'y'})
            self.assertFalse(any('services/' in url for url in calls))

    def test_existing_installations_keep_media_controls_off(self):
        with tempfile.TemporaryDirectory() as directory:
            c = bridge.Connector({'entity_ids': ['media_player.sonos']}, directory)
            self.assertEqual(media.MediaControls(c).selected, set())
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(bridge.BridgeError):
                bridge.Connector({'entity_ids': ['sensor.temp'], 'media_control_entity_ids': ['media_player.sonos']}, directory)

if __name__ == '__main__':
    unittest.main()
