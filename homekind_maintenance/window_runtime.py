"""Paired, bounded window choices and real local component inventory.

Saving/acknowledging a window never authorises an update by itself. Transport
failure is optional; the existing connector maintenance lane keeps running.
"""
from datetime import datetime, timezone
import hashlib
import re
import secrets
from .maintenance import atomic, CLOUD, MaintenanceError
from .windows import WindowJournal, WindowError
from .window_sync import report, reconcile

VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,79}')


class WindowRuntime:
    def __init__(self, companion):
        self.companion = companion
        self.path = companion.directory / 'window-enrolment.json'
        self.journal = None

    def local(self):
        c = self.companion
        credential = c.data.get('credential')
        if not isinstance(credential, dict) or set(credential) != {'id', 'token'}:
            raise WindowError('Pair maintenance before exchanging windows.')
        scope = hashlib.sha256((credential['id'] + ':' + credential['token']).encode()).hexdigest()
        enabled = c.options().get('allow_updates') is True
        old = None
        if self.path.exists():
            import json
            try:
                old = json.loads(self.path.read_text())
                if not isinstance(old, dict) or set(old) != {'schema', 'scope', 'enabled', 'management_id'} or type(old['schema']) is not int or old['schema'] != 1 or type(old['enabled']) is not bool or not isinstance(old['scope'], str) or not re.fullmatch(r'[0-9a-f]{64}', old['scope']) or not isinstance(old['management_id'], str) or not re.fullmatch(r'(?:[0-9a-f]{32})?', old['management_id']) or old['enabled'] != bool(old['management_id']):
                    raise ValueError()
            except (OSError, ValueError, TypeError):
                raise WindowError('Retain the unverified local enrolment.') from None
        if old is None or old['scope'] != scope or old['enabled'] != enabled:
            old = dict(schema=1, scope=scope, enabled=enabled,
                       management_id=secrets.token_hex(16) if enabled else '')
            atomic(self.path, old)
        # Opt-out/re-enrolment cannot acknowledge a previous installation's file.
        self.journal = WindowJournal(c.directory / ('window-' + scope[:24] + '-' + (old['management_id'] or 'off') + '.json'))
        return old

    def inventory(self, executor_ready=False):
        c = self.companion
        rows = []
        paths = [('core', 'core', 'Home Assistant Core', '/core/info'),
                 ('os', 'os', 'Home Assistant OS', '/os/info'),
                 ('supervisor', 'supervisor', 'Home Assistant Supervisor', '/supervisor/info'),
                 ('companion', 'companion', 'HomeKind Maintenance Companion', '/addons/self/info')]
        if c.target and re.fullmatch(r'[0-9a-f]{8}_homekind_bridge', c.target):
            paths.append(('connector', 'connector', 'HomeKind connector', '/addons/' + c.target + '/info'))
        for ident, kind, name, path in paths:
            try:
                info = c.supervisor(path, timeout=2)
                installed, available = info.get('version'), info.get('version_latest')
                installed = installed if isinstance(installed, str) and VERSION.fullmatch(installed) else None
                available = available if isinstance(available, str) and VERSION.fullmatch(available) else None
                if installed is None:
                    continue
                rows.append(dict(id=ident, kind=kind, name=name, installed=installed,
                                 available=available, can_install=executor_ready and kind in ('core','os'), vendor_managed=False))
            except (MaintenanceError, OSError, ValueError):
                continue
        return rows

    def exchange(self, local, inventory, executor_ready=False, execution=None):
        c = self.companion
        payload = report(self.journal, management_id=local['management_id'],
                         locally_enabled=local['enabled'], inventory=inventory,
                         now=datetime.fromtimestamp(c.clock(), timezone.utc),
                         executor_ready=executor_ready, execution=execution)
        credential = dict(c.data['credential'])
        response = c.call(CLOUD + '/maintenance-windows', credential['token'],
                          {'bridge_id': credential['id'], 'report': payload}, timeout=2)
        # Re-read choices/credential after transport, before writing any receipt.
        current = self.local()
        if current != local or c.data.get('credential') != credential:
            raise WindowError('The local maintenance choice changed.')
        return response

    def step(self):
        local = self.local()
        from .system_updates import SystemUpdater
        ready = local['enabled'] and self.companion.options().get('allow_system_updates') is True
        executor = SystemUpdater(self.companion,self.journal)
        inventory = self.inventory(ready)
        response = self.exchange(local, inventory, ready, executor.status())
        if reconcile(self.journal, response, locally_enabled=local['enabled']):
            # A distinct report acknowledges the actual durable file, not a
            # desired revision that has merely arrived over the network.
            response = self.exchange(local, inventory, ready, executor.status())
        def current_authority():
            current = self.local()
            if current != local or self.companion.options().get('allow_system_updates') is not True:
                return False
            r = self.exchange(local,self.inventory(True),True,executor.status())
            data = self.journal.read()
            snapshot = None if data is None else {k:data[k] for k in ('policy','paused','defer_until')}
            return isinstance(r,dict) and set(r)=={'protocol','authority_current','executor_ready','desired'} and type(r['protocol']) is int and r['protocol']==1 and r['authority_current'] is True and r['executor_ready'] is True and r['desired']==snapshot and snapshot is not None
        executor.authorise = current_authority
        if ready and response.get('executor_ready') is True:
            executor.step()
            self.exchange(local,inventory,ready,executor.status())
        return response
