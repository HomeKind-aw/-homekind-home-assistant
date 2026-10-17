"""Finite HA device capabilities. No user-supplied service, URL or arbitrary attributes."""
import math

SENSOR_CLASSES = set('absolute_humidity apparent_power aqi atmospheric_pressure battery blood_glucose_concentration carbon_dioxide carbon_monoxide conductivity current data_rate data_size distance duration energy energy_distance energy_storage frequency gas humidity illuminance irradiance moisture monetary nitrogen_dioxide nitrogen_monoxide nitrous_oxide ozone ph pm1 pm4 pm10 pm25 power power_factor precipitation precipitation_intensity pressure reactive_energy reactive_power signal_strength sound_pressure speed sulphur_dioxide temperature volatile_organic_compounds volatile_organic_compounds_parts voltage volume volume_flow_rate volume_storage water weight wind_direction wind_speed'.split())
BINARY_CLASSES = set('battery battery_charging carbon_monoxide cold connectivity door garage_door gas heat light lock moisture motion moving occupancy opening plug power presence problem running safety smoke sound tamper update vibration window'.split())
EXTRA_DOMAINS = {'cover', 'fan', 'climate', 'humidifier', 'water_heater', 'lock', 'valve', 'alarm_control_panel', 'siren', 'lawn_mower'}
STATES = {
    'cover': {'open', 'closed', 'opening', 'closing'}, 'fan': {'on', 'off'},
    'climate': {'off', 'heat', 'cool', 'heat_cool', 'auto', 'dry', 'fan_only'},
    'humidifier': {'on', 'off'}, 'water_heater': {'off', 'eco', 'electric', 'gas', 'heat_pump', 'high_demand', 'performance'},
    'lock': {'locked', 'unlocked', 'locking', 'unlocking', 'jammed', 'open', 'opening'},
    'valve': {'open', 'closed', 'opening', 'closing'}, 'siren': {'on', 'off'},
    'alarm_control_panel': {'disarmed', 'armed_home', 'armed_away', 'armed_night', 'armed_vacation', 'armed_custom_bypass', 'pending', 'arming', 'disarming', 'triggered'},
    'lawn_mower': {'mowing', 'docked', 'paused', 'error', 'returning'},
}
HVAC_MODES = {'off', 'heat', 'cool', 'heat_cool', 'auto', 'dry', 'fan_only'}
HVAC_ACTIONS = {'off', 'heating', 'cooling', 'drying', 'idle', 'fan', 'defrosting', 'preheating'}
COVER_CONTROLS = {'', 'blind', 'curtain', 'shade', 'shutter', 'awning'}


def number(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def capabilities(state):
    domain = str(state.get('entity_id', '')).split('.')[0]
    attrs = state.get('attributes', {})
    flags = attrs.get('supported_features', 0)
    flags = flags if type(flags) is int and 0 <= flags <= 2147483647 else 0
    actions = []
    result = {'actions': actions}
    if domain in ('light', 'switch'):
        actions += ['on', 'off']
        if domain == 'light' and isinstance(attrs.get('supported_color_modes'), list) and any(mode in ('brightness', 'color_temp', 'hs', 'xy', 'rgb', 'rgbw', 'rgbww', 'white') for mode in attrs['supported_color_modes']):
            actions.append('brightness')
    elif domain == 'cover' and str(attrs.get('device_class', '')) in COVER_CONTROLS:
        actions += [action for bit, action in ((1, 'open'), (2, 'close'), (8, 'stop'), (4, 'position'), (128, 'tilt')) if flags & bit]
    elif domain == 'fan':
        actions += [action for bit, action in ((32, 'on'), (16, 'off'), (1, 'percentage'), (2, 'oscillate')) if flags & bit]
        if 'percentage' in actions:
            step = attrs.get('percentage_step', 1)
            result['percentage_step'] = step if number(step, 0.01, 100) else 1
    elif domain == 'climate':
        modes = attrs.get('hvac_modes')
        if isinstance(modes, list):
            result['hvac_modes'] = list(dict.fromkeys(mode for mode in modes if isinstance(mode, str) and mode in HVAC_MODES))[:7]
            if result['hvac_modes']:
                actions.append('hvac')
        low, high, step = attrs.get('min_temp'), attrs.get('max_temp'), attrs.get('target_temp_step', 1)
        if flags & 1 and number(low, -100, 200) and number(high, -100, 200) and low < high and number(step, 0.01, 10) and attrs.get('temperature_unit') in ('°C', '°F'):
            actions.append('temperature')
            result.update(temperature_min=low, temperature_max=high, temperature_step=step, temperature_unit=attrs['temperature_unit'])
    return result


def attributes(state):
    """Copy only typed position, speed and environment fields, never device secrets."""
    domain = str(state.get('entity_id', '')).split('.')[0]
    attrs = state.get('attributes', {})
    result = {'capabilities': capabilities(state)}
    ranges = {}
    if domain in ('cover', 'valve'):
        ranges = {'current_position': ('position', 0, 100), 'current_tilt_position': ('tilt', 0, 100)}
    elif domain == 'light':
        if number(attrs.get('brightness'), 0, 255):
            result['brightness'] = round(attrs['brightness'] / 255 * 100)
    elif domain == 'fan':
        ranges = {'percentage': ('percentage', 0, 100)}
        if type(attrs.get('oscillating')) is bool:
            result['oscillating'] = attrs['oscillating']
    elif domain in ('climate', 'water_heater'):
        ranges = {'current_temperature': ('current_temperature', -100, 200), 'temperature': ('temperature', -100, 200), 'current_humidity': ('humidity', 0, 100)}
        if attrs.get('hvac_action') in HVAC_ACTIONS:
            result['hvac_action'] = attrs['hvac_action']
        if attrs.get('temperature_unit') in ('°C', '°F'):
            result['temperature_unit'] = attrs['temperature_unit']
    elif domain == 'humidifier':
        ranges = {'humidity': ('humidity', 0, 100), 'current_humidity': ('current_humidity', 0, 100)}
    for source, (target, low, high) in ranges.items():
        if number(attrs.get(source), low, high):
            result[target] = attrs[source]
    return result


def action_request(state, action, parameters=None):
    """Revalidate against the actual local entity immediately before dispatch."""
    domain = str(state.get('entity_id', '')).split('.')[0]
    params = {} if parameters is None else parameters
    caps = capabilities(state)
    if not isinstance(params, dict) or action not in caps['actions']:
        raise ValueError('This device does not support the requested action.')
    need_value = action in ('brightness', 'position', 'tilt', 'percentage', 'oscillate', 'hvac', 'temperature')
    if set(params) != ({'value'} if need_value else set()):
        raise ValueError('Choose the supported device value.')
    value = params.get('value')
    fields = {}
    services = {'on': 'turn_on', 'off': 'turn_off'}
    if action in ('brightness', 'position', 'tilt', 'percentage'):
        if not number(value, 0, 100) or action != 'percentage' and value != round(value):
            raise ValueError('Choose a percentage between zero and one hundred.')
        if action == 'percentage' and value not in (0, 100) and abs(value / caps.get('percentage_step', 1) - round(value / caps.get('percentage_step', 1))) > 0.02:
            raise ValueError('Choose a speed supported by this fan.')
        services.update(brightness='turn_on', position='set_cover_position', tilt='set_cover_tilt_position', percentage='set_percentage')
        fields[{'brightness': 'brightness_pct', 'position': 'position', 'tilt': 'tilt_position', 'percentage': 'percentage'}[action]] = value
    elif action == 'oscillate':
        if type(value) is not bool:
            raise ValueError('Choose oscillation on or off.')
        services[action] = 'oscillate'
        fields['oscillating'] = value
    elif action == 'temperature':
        if not number(value, caps['temperature_min'], caps['temperature_max']) or abs((value - caps['temperature_min']) / caps['temperature_step'] - round((value - caps['temperature_min']) / caps['temperature_step'])) > 0.001:
            raise ValueError('Choose a temperature in this thermostat’s range and step.')
        services[action] = 'set_temperature'
        fields['temperature'] = value
    elif action == 'hvac':
        if value not in caps.get('hvac_modes', []):
            raise ValueError('Choose a supported heating or cooling mode.')
        services[action] = 'set_hvac_mode'
        fields['hvac_mode'] = value
    if domain == 'cover':
        services.update(open='open_cover', close='close_cover', stop='stop_cover')
    service = services.get(action)
    if service is None:
        raise ValueError('Choose a supported device action.')
    return domain, service, fields


def action_matches(state, action, parameters=None):
    params = parameters or {}
    value = state.get('state')
    attrs = attributes(state)
    if action in ('on', 'off'):
        return value == action
    if action in ('open', 'close'):
        return value == ('open' if action == 'open' else 'closed')
    if action == 'hvac':
        return value == params.get('value')
    if action == 'stop':
        return False  # HA has no persistent "stop succeeded" state.
    target = params.get('value')
    actual = attrs.get('oscillating' if action == 'oscillate' else action)
    return type(actual) is bool and type(target) is bool and actual == target if action == 'oscillate' else number(actual, -100, 200) and number(target, -100, 200) and abs(actual - target) <= (1 if action == 'brightness' else 0.05)
