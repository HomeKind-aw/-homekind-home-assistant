"""Inactive pure settings/report adapter. No network or update operation.

The matched runner must supply freshly checked local consent and authenticated
server responses. Current Docker/Companion imports neither this nor windows.py.
"""
from datetime import datetime
import re
from .windows import WindowError, instant, occurrences


def reconcile(journal, response, *, locally_enabled):
    if not isinstance(response, dict) or set(response) != {'protocol', 'authority_current', 'executor_ready', 'desired'}:
        raise WindowError('Unsupported update-window response.')
    if type(response['protocol']) is not int or response['protocol'] != 1 or type(response['executor_ready']) is not bool or type(response['authority_current']) is not bool:
        raise WindowError('Unsupported update-window protocol.')
    if locally_enabled is not True or response['authority_current'] is not True:
        return False
    desired = response['desired']
    if desired is None:
        return False
    current = journal.read()
    journal.reconcile(desired, expected_revision=current['policy']['revision'] if current else 0,
                      locally_enabled=locally_enabled, authority_current=True)
    return True


def report(journal, *, management_id, locally_enabled, inventory, now, executor_ready=False, execution=None):
    # Inventory remains metadata supplied by a future verified collector. The
    # server checks its bounded component schema; it never authorises a target.
    if not isinstance(management_id, str) or not re.fullmatch(r'(?:[0-9a-f]{32})?', management_id) or type(locally_enabled) is not bool or locally_enabled and not management_id:
        raise WindowError('Check the local management choice.')
    if not isinstance(inventory, list) or len(inventory) > 128:
        raise WindowError('Use a bounded component inventory.')
    seen = set()
    for item in inventory:
        if not isinstance(item, dict) or set(item) != {'id', 'kind', 'name', 'installed', 'available', 'can_install', 'vendor_managed'}:
            raise WindowError('Use defined component metadata only.')
        key = item['id']
        if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_.:-]{0,149}', key) or key in seen:
            raise WindowError('Use distinct known component identifiers.')
        seen.add(key)
        if item['kind'] not in ('connector', 'companion', 'core', 'os', 'supervisor', 'addon', 'device', 'vendor') or type(item['can_install']) is not bool or type(item['vendor_managed']) is not bool:
            raise WindowError('Check the component capabilities.')
        if not isinstance(item['name'], str) or not 1 <= len(item['name']) <= 80 or any(ord(c) < 32 or ord(c) == 127 for c in item['name']):
            raise WindowError('Use a short component name.')
        for version in (item['installed'], item['available']):
            if version is not None and (not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,79}', version)):
                raise WindowError('Use component versions without URLs or credentials.')
    clock = instant(now)
    data = journal.read()
    applied = None if data is None else {key: data[key] for key in ('policy', 'paused', 'defer_until')}
    upcoming = None
    if data and locally_enabled and data['policy']['enabled'] and not data['paused']:
        defer = instant(datetime.fromisoformat(data['defer_until'])) if data['defer_until'] else clock
        window = next((w for w in occurrences(data['policy'], clock) if w['start'] >= max(clock, defer)), None)
        if window:
            upcoming = {'revision': data['policy']['revision'], 'start': window['start'].isoformat(), 'end': window['end'].isoformat()}
    if type(executor_ready) is not bool:
        raise WindowError('Use a verified executor capability.')
    result = {'protocol': 1, 'management_id': management_id, 'locally_enabled': locally_enabled,
            'applied_revision': data['policy']['revision'] if data else 0, 'applied': applied,
            'inventory': inventory, 'next_window': upcoming, 'executor_ready': executor_ready}
    if execution is not None:
        from .system_updates import VERSION, ACTIVE
        from uuid import UUID
        if not isinstance(execution,dict) or set(execution) != {'id','component','before','target','phase','created_at','backup_id'} or str(UUID(execution['id'])) != execution['id'] or execution['component'] not in ('core','os') or execution['phase'] not in ACTIVE | {'backup_dispatch','update_dispatch','reboot_dispatch','healthy','unknown','failed','cancelled'} or any(not isinstance(execution[k],str) or not VERSION.fullmatch(execution[k]) for k in ('before','target')) or type(execution['created_at']) not in (int,float) or execution['backup_id'] is not None and (not isinstance(execution['backup_id'],str) or not re.fullmatch(r'[a-z0-9]{8,64}',execution['backup_id'])):
            raise WindowError('Use bounded maintenance status only.')
        result['execution'] = execution
    return result
