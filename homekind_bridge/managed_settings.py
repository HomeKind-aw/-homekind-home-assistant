"""Versioned feature settings, bounded by the installer's local device selection."""
import re

PROTOCOL = 1
FIELDS = {"read_only", "live_camera_entity_ids", "media_control_entity_ids"}
MAX_REVISION = 2147483647


class SettingsError(ValueError):
    pass


def normalise(options):
    if not isinstance(options, dict) or type(options.get("settings_version", 1)) is not int or options.get("settings_version", 1) != 1:
        raise SettingsError("These settings need a compatible connector version.")
    # Absent fields never authorise new access. Credentials are stored separately.
    result = {"settings_version": 1, "pairing_code": options.get("pairing_code", ""),
              "entity_ids": options.get("entity_ids", []), "read_only": options.get("read_only", True) is not False,
              "live_camera_entity_ids": options.get("live_camera_entity_ids", []),
              "media_control_entity_ids": options.get("media_control_entity_ids", []),
              "managed_settings": options.get("managed_settings") is True,
              "management_id": options.get("management_id", ""), "applied_revision": options.get("applied_revision", 0)}
    if not isinstance(result["entity_ids"], list) or len(result["entity_ids"]) > 250 or any(not isinstance(e, str) or not re.fullmatch(r"(sensor|binary_sensor|light|switch|camera|event|media_player|weather|vacuum|cover|fan|climate|humidifier|water_heater|lock|valve|alarm_control_panel|siren|lawn_mower)\.[a-z0-9_]{1,150}", e) for e in result["entity_ids"]):
        raise SettingsError("Check the selected home devices locally.")
    if len(set(result["entity_ids"])) != len(result["entity_ids"]):
        raise SettingsError("Choose each home device once.")
    validate_config({key: result[key] for key in FIELDS}, result["entity_ids"])
    if not isinstance(result["management_id"], str) or (result["management_id"] and not re.fullmatch(r"[0-9a-f]{32}", result["management_id"])):
        raise SettingsError("Check the saved management choice locally.")
    if result["managed_settings"] != bool(result["management_id"]):
        raise SettingsError("Enable management once in the local setup screen.")
    if type(result["applied_revision"]) is not int or not 0 <= result["applied_revision"] <= MAX_REVISION:
        raise SettingsError("Check the saved settings revision locally.")
    return result


def validate_config(config, selected):
    if not isinstance(config, dict) or set(config) != FIELDS or type(config["read_only"]) is not bool:
        raise SettingsError("Choose supported home connection features.")
    for field, domain in (("live_camera_entity_ids", "camera"), ("media_control_entity_ids", "media_player")):
        entities = config[field]
        if not isinstance(entities, list) or len(entities) > 20 or any(not isinstance(e, str) or not re.fullmatch(domain + r"\.[a-z0-9_]{1,150}", e) or e not in selected for e in entities) or len(set(entities)) != len(entities):
            raise SettingsError("Choose features only for devices selected during installation.")
    return {key: sorted(config[key]) if isinstance(config[key], list) else config[key] for key in sorted(FIELDS)}


def apply(options, response):
    current = normalise(options)
    if not current["managed_settings"] or response is None:
        return None
    if not isinstance(response, dict) or set(response) != {"protocol", "management_id", "revision", "config"}:
        raise SettingsError("The connection settings response could not be checked.")
    if type(response["protocol"]) is not int or response["protocol"] != PROTOCOL or response["management_id"] != current["management_id"]:
        raise SettingsError("This settings request belongs to a different setup choice.")
    revision = response["revision"]
    if type(revision) is not int or not 1 <= revision <= MAX_REVISION:
        raise SettingsError("The connection settings revision could not be checked.")
    if revision <= current["applied_revision"]:
        return None
    config = validate_config(response["config"], current["entity_ids"])
    return {**current, **config, "applied_revision": revision}
