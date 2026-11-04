#!/usr/bin/env python3
"""Create private ESPHome secrets locally without printing credentials."""
import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import secrets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    ssid = input("2.4 GHz Wi-Fi name: ").strip()
    password = getpass.getpass("Wi-Fi password (hidden): ")
    if not ssid or len(ssid.encode()) > 32 or not password or len(password) > 64:
        parser.error("Check the Wi-Fi name and password.")
    values = {
        "wifi_ssid": ssid,
        "wifi_password": password,
        "api_encryption_key": base64.b64encode(secrets.token_bytes(32)).decode(),
        "fallback_ap_password": secrets.token_urlsafe(18),
    }
    destination = args.directory / "secrets.yaml"
    # Refuse replacement, including symlinks: an upgrade must retain pairing keys.
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write("# Private local installation values. Keep out of Git and chat.\n")
        for key, value in values.items():
            stream.write(f"{key}: {json.dumps(value, ensure_ascii=False)}\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(f"Created {destination.name} with owner-only permissions. Existing files are never replaced.")
    print("The ESPHome API encryption key is in that private file for local Home Assistant pairing.")


if __name__ == "__main__":
    main()
