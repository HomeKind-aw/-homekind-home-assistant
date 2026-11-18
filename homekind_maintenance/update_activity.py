"""Local-only activity classification. Never follow or export media URLs."""
import ipaddress
import re
from urllib.parse import unquote, urlsplit

# A bounded local response only. Names and source identifiers never enter a
# cloud report; the updater exports its existing finite readiness reason.
IDLE_TEMPLATE = r'''{% set ns = namespace(scripts=[], players=[]) %}
{% for s in (states.script | selectattr('state','eq','on') | list)[:65] %}
{% set ns.scripts = ns.scripts + [{'entity_id': s.entity_id, 'name': s.name}] %}
{% endfor %}
{% for p in (states.media_player | selectattr('state','in',['playing','buffering']) | list)[:65] %}
{% set ns.players = ns.players + [{'entity_id': p.entity_id, 'name': p.name,
 'source': p.attributes.media_content_id | default('', true)}] %}
{% endfor %}
{{ {'scripts': ns.scripts, 'players': ns.players} | to_json }}'''


def safe_label(value):
    return isinstance(value, str) and 0 < len(value) <= 160 and all(ord(c) >= 32 and ord(c) != 127 for c in value)


def source_kind(source, origins):
    if not isinstance(source, str) or not source or len(source) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in source):
        return 'unknown'
    decoded = unquote(unquote(source)).lower()
    if decoded.startswith('media-source:'):
        return 'core'
    if any(marker in decoded for marker in ('/api/', '/media/', '/local/', '/tts_proxy/', 'authsig=')):
        return 'core'
    if decoded.startswith('spotify:') or re.match(r'^x-sonosapi-[a-z0-9-]+:', decoded) or decoded.startswith('x-rincon-stream:'):
        return 'independent'
    if decoded.startswith('x-rincon-mp3radio://'):
        return source_kind(source[len('x-rincon-mp3radio://'):], origins)
    try:
        url = urlsplit(source)
        host = (url.hostname or '').lower().rstrip('.')
        port = url.port or (443 if url.scheme == 'https' else 80)
        if url.scheme not in ('http', 'https') or not host or url.username or url.password:
            return 'unknown'
        if host in origins or port == 8123:
            return 'core'
        try:
            if not ipaddress.ip_address(host).is_global:
                return 'unknown'
        except ValueError:
            if '.' not in host or host.endswith(('.local', '.internal', '.lan', '.home', '.localhost', '.localdomain')):
                return 'unknown'
        if port not in (80, 443):
            return 'unknown'
        return 'independent'
    except ValueError:
        return 'unknown'


def activity_check(value, core, config):
    empty = {'scripts': [], 'players': [], 'background_audio': 0}
    # Accept a still-idle legacy boolean response; a false value remains a hold.
    if type(value) is bool:
        return ('ready' if value else 'home_in_use'), empty
    if not isinstance(value, dict) or set(value) != {'scripts', 'players'}:
        raise ValueError('Unverified local activity response.')
    scripts, players = value['scripts'], value['players']
    if not isinstance(scripts, list) or not isinstance(players, list) or len(scripts) > 64 or len(players) > 64:
        raise ValueError('Unverified local activity bounds.')
    for collection, domain, keys in ((scripts, 'script', {'entity_id', 'name'}), (players, 'media_player', {'entity_id', 'name', 'source'})):
        for item in collection:
            if not isinstance(item, dict) or set(item) != keys or not safe_label(item['name']) or not isinstance(item['entity_id'], str) or not re.fullmatch(domain + r'\.[a-z0-9_]{1,140}', item['entity_id']):
                raise ValueError('Unverified local activity metadata.')
    detail = {'scripts': [s['name'] for s in scripts[:16]], 'players': [], 'background_audio': 0}
    if scripts:
        return 'script_running', detail
    if not players:
        return 'ready', detail
    if not isinstance(config, dict):
        raise ValueError('Unverified local Home Assistant configuration.')
    origins = {'homeassistant', 'homeassistant.local', str(core.get('ip_address', '')).lower()}
    for field in ('internal_url', 'external_url'):
        candidate = config.get(field)
        if candidate:
            if not isinstance(candidate, str):
                raise ValueError('Unverified local Home Assistant address.')
            origins.add((urlsplit(candidate).hostname or '').lower().rstrip('.'))
    kinds = []
    for player in players:
        kind = source_kind(player['source'], origins)
        if kind == 'independent':
            detail['background_audio'] += 1
        else:
            kinds.append(kind)
            if len(detail['players']) < 16:
                detail['players'].append(player['name'])
    return ('home_in_use' if 'core' in kinds else 'media_unverified' if kinds else 'ready'), detail
