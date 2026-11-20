"""Local running-script check. Audio playback never delays an approved update."""
import re

# Read only running scripts. Media players and their URLs are deliberately not
# queried, validated or classified: even missing media metadata cannot veto a
# scheduled update. Script names stay in the bounded local diagnostic page.
IDLE_TEMPLATE = r'''{% set ns = namespace(scripts=[]) %}
{% for s in (states.script | selectattr('state','eq','on') | list)[:65] %}
{% set ns.scripts = ns.scripts + [{'entity_id': s.entity_id, 'name': s.name}] %}
{% endfor %}
{{ {'scripts': ns.scripts} | to_json }}'''


def safe_label(value):
    return isinstance(value, str) and 0 < len(value) <= 160 and all(ord(c) >= 32 and ord(c) != 127 for c in value)


def activity_check(value):
    empty = {'scripts': [], 'players': [], 'background_audio': 0}
    # Retain the old idle response for compatible local adapters. An ambiguous
    # false response cannot verify whether a script is running, so stays held.
    if type(value) is bool:
        return ('ready' if value else 'readiness_unavailable'), empty
    if not isinstance(value, dict) or set(value) != {'scripts'}:
        raise ValueError('Unverified local script response.')
    scripts = value['scripts']
    if not isinstance(scripts, list) or len(scripts) > 64:
        raise ValueError('Unverified local script bounds.')
    for item in scripts:
        if not isinstance(item, dict) or set(item) != {'entity_id', 'name'} or not safe_label(item['name']) or not isinstance(item['entity_id'], str) or not re.fullmatch(r'script\.[a-z0-9_]{1,140}', item['entity_id']):
            raise ValueError('Unverified local script metadata.')
    detail = {**empty, 'scripts': [s['name'] for s in scripts[:16]]}
    return ('script_running' if scripts else 'ready'), detail
