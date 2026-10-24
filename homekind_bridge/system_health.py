"""Read-only compatibility observations, isolated from readings and controls.

Only versions and aggregate checks leave the home. No device names, values,
network keys, backup secrets or remotely supplied HA operations are accepted.
"""
import hashlib
import logging
import re
import threading
import time
import uuid

from bridge import BridgeError, CLOUD_URL, VERSION

HEALTH_URL = CLOUD_URL.removesuffix('/homekind-home-bridge') + '/homekind-system-health'
VERSION_TEXT = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+() /:-]{0,79}')


def version(value):
    return value if isinstance(value, str) and '://' not in value and VERSION_TEXT.fullmatch(value) else None


def component(key, kind, installed=None, latest=None, updating=False, product=None):
    installed, latest = version(installed), version(latest)
    result = {'key': key, 'kind': kind, 'installed': installed, 'latest': latest,
            'pending': bool(installed and latest and installed != latest),
            'updating': updating is True}
    if isinstance(product, str) and '://' not in product and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._()+/:-]{0,79}', product):
        result['product'] = product
    return result


def project(config, states, entries, devices, registry, selected, stream, queue_size):
    """Project local HA responses into bounded operational metadata."""
    if not isinstance(config, dict) or not isinstance(states, list) or len(states) > 20000:
        raise BridgeError('System checks are temporarily unavailable.')
    rows = {s['entity_id']: s for s in states if isinstance(s, dict) and isinstance(s.get('entity_id'), str)}
    components = [component('homekind_connector', 'connector', VERSION),
                  component('home_assistant_core', 'core', config.get('version'))]
    metadata = {r['entity_id']: r for r in registry or [] if isinstance(r, dict) and isinstance(r.get('entity_id'), str)}
    device_map = {d['id']: d for d in devices or [] if isinstance(d, dict) and isinstance(d.get('id'), str)}
    integrations = {e.get('domain'): e for e in entries or [] if isinstance(e, dict) and e.get('domain') in ('zha', 'mqtt')}
    zigbee = 'unverified'
    if 'zha' in integrations:
        zigbee = 'ready' if integrations['zha'].get('state') == 'loaded' else 'unavailable'
        components.append(component('zigbee_zha', 'integration', config.get('version')))
    # MQTT alone does not establish the presence or health of Zigbee2MQTT.
    z2m = {d['id'] for d in device_map.values() if str(d.get('manufacturer', '')).lower() == 'zigbee2mqtt' and str(d.get('model', '')).lower() == 'bridge'}
    if z2m:
        connected = [s for entity, s in rows.items() if metadata.get(entity, {}).get('device_id') in z2m and s.get('attributes', {}).get('device_class') == 'connectivity']
        if connected:
            zigbee = 'ready' if all(s.get('state') == 'on' for s in connected) else 'unavailable'
    elif entries is not None and devices is not None and 'zha' not in integrations:
        zigbee = 'not_used'
    for entity, state in sorted(rows.items()):
        if not entity.startswith('update.'):
            continue
        meta, attrs = metadata.get(entity, {}), state.get('attributes', {})
        if not isinstance(attrs, dict):
            continue
        platform = meta.get('platform')
        # Versions only; an update title can contain a resident's room/device name.
        key = 'update_' + hashlib.sha256(entity.encode()).hexdigest()[:24]
        kind = 'firmware' if platform in ('zha', 'esphome', 'matter', 'zwave_js') else 'addon' if platform == 'hassio' else 'integration'
        components.append(component(key, kind, attrs.get('installed_version'), attrs.get('latest_version'), attrs.get('in_progress') is True or type(attrs.get('in_progress')) is int and attrs['in_progress'] > 0, product={'zha':'Zigbee firmware','hassio':'Home Assistant app','esphome':'ESPHome firmware','mqtt':'MQTT update'}.get(platform)))
        if len(components) >= 120:
            break
    # Device registry firmware versions also cover devices without update entities.
    selected_devices = {metadata[e].get('device_id') for e in selected if e in metadata}
    for ident in sorted(selected_devices | z2m, key=str):
        d = device_map.get(ident, {})
        if version(d.get('sw_version')):
            product = ' '.join(str(d.get(k) or '') for k in ('manufacturer', 'model')).strip()[:80]
            components.append(component('device_' + hashlib.sha256(str(ident).encode()).hexdigest()[:24], 'firmware', d['sw_version'], product=product))
        if len(components) >= 160:
            break
    missing = sum(e not in rows for e in selected if not e.startswith('event.homekind_'))
    return {'components': components, 'checks': {'ha_api': True, 'stream': stream is True,
            'missing_entities': missing, 'queue_size': max(0, min(5000, int(queue_size))),
            'zigbee': zigbee, 'registry': registry is not None and entries is not None}}


class SystemHealth(threading.Thread):
    def __init__(self, connector):
        super().__init__(name='homekind-system-checks', daemon=True)
        self.c = connector
        self.session, self.sequence = str(uuid.uuid4()), 0

    def registries(self):
        # Fixed read-only queries; no cloud-provided commands or identifiers.
        from device_onboarding import LocalHome
        local = LocalHome(self.c)
        try:
            with local.socket() as ws:
                local.auth(ws)
                registry = local.query(ws, 1, 'config/entity_registry/list')
                devices = local.query(ws, 2, 'config/device_registry/list')
                entries = local.query(ws, 3, 'config_entries/get')
                if not all(isinstance(x, list) and len(x) <= 20000 for x in (registry, devices, entries)):
                    raise BridgeError('System metadata is unavailable.')
                return entries, devices, registry
        except Exception:
            return None, None, None

    def snapshot(self):
        start = time.monotonic()
        try:
            config = self.c.ha('config', timeout=5)
            states = self.c.ha('states', timeout=5)
            latency = round((time.monotonic() - start) * 1000)
            entries, devices, registry = self.registries()
            with self.c.lock:
                selected, stream, queue_size = set(self.c.allowlist), self.c.stream_connected, len(self.c.pending)
            report = project(config, states, entries, devices, registry, selected, stream, queue_size)
            report['checks']['api_latency_ms'] = min(120000, max(0, latency))
        except Exception:
            report = {'components': [component('homekind_connector', 'connector', VERSION)],
                      'checks': {'ha_api': False, 'stream': False, 'missing_entities': 0,
                                 'queue_size': 0, 'zigbee': 'unverified', 'registry': False, 'api_latency_ms': None}}
        self.sequence += 1
        return {'schema': 1, 'session': self.session, 'sequence': self.sequence,
                'observed_at': self.c.stamp(), **report}

    def once(self):
        report = self.snapshot()
        self.c.request(HEALTH_URL, self.c.credentials['token'],
                       {'source': 'connector', 'bridge_id': self.c.credentials['bridge_id'], 'report': report}, timeout=8)
        return any(c['updating'] for c in report['components'])

    def run(self):
        delay = 300
        while not self.c.stopping():
            if not self.c.ready.wait(.5):
                continue
            try:
                delay = 60 if self.once() else 300
            except Exception:
                # Optional diagnostics never terminate, delay or retry a control.
                logging.info('System checks are waiting; home readings and controls continue.')
                delay = 300
            end = time.monotonic() + delay
            while not self.c.stopping() and time.monotonic() < end:
                self.c.halt.wait(min(1, max(0, end - time.monotonic())))
