import importlib.util
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'homekind_bridge'))
import system_health as health
spec = importlib.util.spec_from_file_location('maintenance_health_test', ROOT / 'homekind_maintenance/maintenance.py')
maintenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)


class SystemHealthTests(unittest.TestCase):
    def project(self, states, entries=None, devices=None, registry=None, selected=None):
        return health.project({'version': '2026.10.3'}, states, entries, devices, registry, selected or set(), True, 0)

    def test_quiet_sensor_and_off_tv_are_not_failed_for_an_old_timestamp(self):
        states = [{'entity_id': 'binary_sensor.door', 'state': 'off', 'last_changed': '2020-01-01T00:00:00Z'}, {'entity_id': 'media_player.tv', 'state': 'off', 'last_changed': '2020-01-01T00:00:00Z'}]
        result = self.project(states, selected={s['entity_id'] for s in states})
        self.assertEqual(result['checks']['missing_entities'], 0)
        self.assertTrue(result['checks']['ha_api'])

    def test_removed_entities_are_detected_without_exporting_identifiers_or_values(self):
        result = self.project([], selected={'binary_sensor.private_room'})
        self.assertEqual(result['checks']['missing_entities'], 1)
        self.assertNotIn('private_room', str(result))

    def test_versions_and_update_progress_are_projected_without_names_urls_or_keys(self):
        states = [{'entity_id': 'update.private_room', 'state': 'on', 'attributes': {'friendly_name': 'Private resident name', 'installed_version': '1.0.0', 'latest_version': '1.0.1', 'in_progress': 35, 'release_url': 'https://secret.invalid/key'}}]
        registry = [{'entity_id': 'update.private_room', 'platform': 'zha'}]
        result = self.project(states, registry=registry)
        update = result['components'][-1]
        self.assertEqual(update['kind'], 'firmware')
        self.assertTrue(update['pending'])
        self.assertTrue(update['updating'])
        for text in ('private_room', 'Private resident', 'secret.invalid'):
            self.assertNotIn(text, str(result))
        self.assertIsNone(health.version('https://secret.invalid/1'))

    def test_zha_and_zigbee2mqtt_need_actual_integration_evidence(self):
        self.assertEqual(self.project([], entries=[{'domain': 'zha', 'state': 'loaded'}])['checks']['zigbee'], 'ready')
        self.assertEqual(self.project([], entries=[{'domain': 'zha', 'state': 'setup_retry'}])['checks']['zigbee'], 'unavailable')
        self.assertEqual(self.project([], entries=[{'domain': 'mqtt', 'state': 'loaded'}])['checks']['zigbee'], 'unverified')
        self.assertEqual(self.project([], entries=[], devices=[], registry=[])['checks']['zigbee'], 'not_used')
        devices = [{'id': 'bridge', 'manufacturer': 'Zigbee2MQTT', 'model': 'Bridge'}]
        registry = [{'entity_id': 'binary_sensor.bridge_connection', 'device_id': 'bridge'}]
        for state, expected in [('on', 'ready'), ('off', 'unavailable')]:
            states = [{'entity_id': 'binary_sensor.bridge_connection', 'state': state, 'attributes': {'device_class': 'connectivity'}}]
            self.assertEqual(self.project(states, devices=devices, registry=registry)['checks']['zigbee'], expected)

    def test_partial_or_local_backup_is_not_an_off_device_system_recovery_point(self):
        self.assertFalse(maintenance.backup_projection({}, {'type': 'partial', 'homeassistant': '2026.10.3'})['full'])
        local = maintenance.backup_projection({}, {'type': 'full', 'homeassistant': '2026.10.3', 'date': '2026-10-25T00:00:00Z', 'locations': [None, '.local'], 'protected': True})
        self.assertFalse(local['off_device'])
        remote = maintenance.backup_projection({}, {'type': 'full', 'homeassistant': '2026.10.3', 'date': '2026-10-25T00:00:00Z', 'locations': ['.local', 'network_backup'], 'protected': True, 'password': 'must-not-leave-home'})
        self.assertTrue(remote['off_device'])
        self.assertNotIn('must-not-leave-home', str(remote))

    def test_companion_inventory_only_uses_get_and_does_not_export_addon_options(self):
        class Companion:
            token = 'local-only'
            calls = []
            def call(self, url, token, payload=None, timeout=5):
                self.calls.append((url, payload))
                if url.endswith('/addons'):
                    return {'result': 'ok', 'data': {'addons': [{'slug': 'core_mosquitto', 'version': '6.0.0', 'version_latest': '6.0.1', 'options': {'password': 'secret'}}]}}
                if url.endswith('/backups/info'):
                    return {'result': 'ok', 'data': {'backups': []}}
                return {'result': 'ok', 'data': {'version': '2026.10.3', 'version_latest': '2026.10.4'}}
        companion = Companion()
        report = maintenance.InventoryReporter(companion).snapshot()
        self.assertTrue(report['inventory_complete'])
        self.assertTrue(all(payload is None for _, payload in companion.calls))
        self.assertNotIn('secret', str(report))
        self.assertIsNone(report['backup']['last_at'])


if __name__ == '__main__':
    unittest.main()
