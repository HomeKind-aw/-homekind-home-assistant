#!/usr/bin/env python3
"""Read-only Home Assistant voice checks. Never records audio or calls a service."""
import argparse
import getpass
import ipaddress
import json
import os
import re
from urllib.parse import urlsplit, urlunsplit


def websocket_url(value):
    url = urlsplit(value)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
        raise ValueError("Use the Home Assistant base address without credentials, a path or query.")
    try:
        local = ipaddress.ip_address(url.hostname).is_private or ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        local = url.hostname == "localhost" or url.hostname.endswith(".local")
    if url.scheme == "http" and not local:
        raise ValueError("Use HTTPS for a remote Home Assistant connection.")
    return urlunsplit(("wss" if url.scheme == "https" else "ws", url.netloc, "/api/websocket", "", ""))


def inspect_snapshot(config, devices, entities, pipelines, device_id=None):
    """Return configuration evidence only; no device or speech success is inferred."""
    checks = []

    def add(name, status, detail):
        checks.append({"check": name, "status": status, "detail": detail})

    components = config.get("components", [])
    add("Home Assistant", "found", str(config.get("version", "Version unavailable")))
    for component in ("esphome", "assist_pipeline", "wyoming"):
        add(component, "found" if component in components else "setup", "Loaded" if component in components else "Not loaded; check the local voice setup")

    ready = [p for p in pipelines if p.get("stt_engine") and p.get("tts_engine") and p.get("conversation_engine")]
    add("Speech pipeline", "found" if ready else "setup", f"{len(ready)} pipeline(s) have speech recognition, a conversation agent and speech output")
    local = [p for p in ready if all(str(p.get(k, "")).startswith("wyoming") for k in ("stt_engine", "tts_engine")) and p.get("conversation_engine") == "conversation.home_assistant"]
    add("Local Assist candidate", "found" if local else "check", f"{len(local)} pipeline(s) use Wyoming speech services and Home Assistant; verify the Wyoming servers are local")

    candidates = [d for d in devices if "homekind-voice" in str(d.get("name", "")).lower() or "voices3r" in str(d.get("model", "")).lower() or "echos3r" in str(d.get("model", "")).lower()]
    device = next((d for d in devices if d.get("id") == device_id), None) if device_id else None
    if not device_id:
        add("Room endpoint", "setup", "Select the arrived device by --device-id; a similar name is not proof of identity")
    elif device is None:
        add("Room endpoint", "attention", "That device is not in this Home Assistant registry")
    else:
        add("Room endpoint", "found", str(device.get("name_by_user") or device.get("name") or "Selected device"))
        add("Room assignment", "found" if device.get("area_id") else "setup", "Area assigned" if device.get("area_id") else "Assign the agreed room in Home Assistant")
        attached = [e for e in entities if e.get("device_id") == device_id and not e.get("disabled_by")]
        domains = {e.get("entity_id", "").split(".")[0] for e in attached}
        add("Speaker entity", "found" if "media_player" in domains else "setup", "Registered media player" if "media_player" in domains else "No enabled media player registered")
        mutes = [e["entity_id"] for e in attached if e.get("entity_id", "").startswith("switch.") and "mute" in e.get("entity_id", "")]
        add("Mute control", "found" if mutes else "setup", "Registered microphone mute control" if mutes else "No enabled microphone mute control found")

    add("Assigned device pipeline", "manual", "Confirm this device uses the selected pipeline in its Voice Assistant settings")
    add("Microphone and speaker", "physical test required", "A registry entry cannot prove wake-word detection, recognition or audible playback")
    add("HomeKind conversation", "not commissioned", "Requires its authenticated endpoint binding and matched HomeKind bridge; native Assist is a separate test")
    return {"checks": checks, "candidate_device_ids": [d["id"] for d in candidates if d.get("id")], "physical_verified": False}


def read_snapshot(url, token):
    from websockets.sync.client import connect

    with connect(websocket_url(url), open_timeout=8, close_timeout=2, max_size=4_000_000, proxy=None) as socket:
        first = json.loads(socket.recv(timeout=8))
        if first.get("type") != "auth_required":
            raise ValueError("Home Assistant did not request authentication.")
        socket.send(json.dumps({"type": "auth", "access_token": token}))
        if json.loads(socket.recv(timeout=8)).get("type") != "auth_ok":
            raise ValueError("Home Assistant sign-in was not accepted.")
        results = []
        for ident, operation in enumerate(("get_config", "config/device_registry/list", "config/entity_registry/list", "assist_pipeline/pipeline/list"), 1):
            socket.send(json.dumps({"id": ident, "type": operation}))
            result = json.loads(socket.recv(timeout=8))
            if result.get("id") != ident or result.get("success") is not True:
                raise ValueError(f"Read-only check {operation} was unavailable; check the local installer access.")
            results.append(result["result"])
        return results[0], results[1], results[2], results[3].get("pipelines", [])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ha-url", default="http://homeassistant.local:8123")
    parser.add_argument("--device-id")
    args = parser.parse_args()
    if args.device_id and not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", args.device_id):
        parser.error("Use the device registry ID shown in Home Assistant.")
    try:
        websocket_url(args.ha_url)
        token = os.environ.get("HOMEKIND_HA_TOKEN") or getpass.getpass("Local Home Assistant token (hidden): ")
        if not token or len(token) > 10000 or any(c.isspace() for c in token):
            raise ValueError("Check the local Home Assistant token.")
        result = inspect_snapshot(*read_snapshot(args.ha_url, token), args.device_id)
    except Exception as error:
        # Library exceptions can include URLs or headers; do not print them.
        print(json.dumps({"status": "unverified", "detail": str(error) if isinstance(error, ValueError) else "The read-only local connection could not be completed. Check the address, installer access and local network.", "physical_verified": False}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
