"""Finite, read-only multi-state observations. No device settings or MQTT commands."""
import re
import hashlib

OBJECT_ACTIONS = frozenset(("movement", "vibration", "orientation", "triple_tap", "fall", "static"))
OBJECT_ORIENTATIONS = frozenset(("face_up", "face_down", "vertical", "tilt"))
OBJECT_POSTURES = frozenset(("normal", "abnormal"))


def multistate_class(state):
    entity = state.get("entity_id", "")
    attrs = state.get("attributes", {})
    if not isinstance(entity, str) or not isinstance(attrs, dict):
        return None
    klass = attrs.get("device_class", state.get("device_class", ""))
    if entity.startswith("event."):
        # Zigbee2MQTT advertises its event types. A button's ordinary presses and
        # Home Assistant motion events must retain their separate meanings.
        declared = attrs.get("event_types")
        if klass == "object_action":
            return klass
        if isinstance(declared, list) and 1 <= len(declared) <= 6 and all(isinstance(x, str) and x in OBJECT_ACTIONS for x in declared) and any(x in declared for x in ("movement", "vibration", "orientation", "fall")):
            return "object_action"
    if entity.startswith("sensor.") and klass in ("", "enum", "object_orientation", "object_posture"):
        for suffix, kind, values in (("orientation", "object_orientation", OBJECT_ORIENTATIONS), ("device_posture", "object_posture", OBJECT_POSTURES)):
            if re.search(r"(?:^|_)" + suffix + r"$", entity.split(".", 1)[1]):
                declared = attrs.get("options")
                if declared is None or isinstance(declared, list) and 1 <= len(declared) <= len(values) and all(isinstance(x, str) and x in values for x in declared):
                    return kind
    return None


def multistate_values(kind):
    return {"object_orientation": OBJECT_ORIENTATIONS, "object_posture": OBJECT_POSTURES}.get(kind)


def object_action(value):
    return value if isinstance(value, str) and value in OBJECT_ACTIONS else None


def object_candidates(states, stamp, metadata=None, allowed=None):
    """Offer a P100's existing native MQTT device actions as a selected reading.

    Device model is local registry metadata. On stream restart the previously
    confirmed opaque identifier is sufficient; it never widens the allowlist.
    """
    rows = []
    metadata, allowed = metadata or {}, allowed or set()
    native_devices = {metadata.get(s.get('entity_id'), {}).get('ha_device_id') for s in states if isinstance(s, dict) and multistate_class(s) == 'object_action'} - {None, ''}
    for source in states:
        if not isinstance(source, dict) or not isinstance(source.get('attributes', {}), dict):
            continue
        entity = source.get('entity_id', '')
        if not isinstance(entity, str) or not re.fullmatch(r'sensor\.[a-z0-9_]{1,150}_battery', entity):
            continue
        attrs = source.get('attributes', {})
        virtual = 'event.homekind_object_' + hashlib.sha256(entity.encode()).hexdigest()[:16]
        identity = str(metadata.get(entity, {}).get('ha_model', '')) + ' ' + str(attrs.get('friendly_name', ''))
        if virtual not in allowed and not re.search(r'\b(P100|DWZTCGQ11LM)\b', identity, re.I):
            continue
        if metadata.get(entity, {}).get('ha_device_id') in native_devices:
            continue
        rows.append({'entity_id': virtual, 'state': 'unknown', 'last_updated': stamp, 'last_changed': stamp,
                     'attributes': {'friendly_name': 'Aqara P100 object actions', 'device_class': 'object_action', 'source_entity_id': entity, 'event_type': ''}})
    return rows
