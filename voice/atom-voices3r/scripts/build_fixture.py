#!/usr/bin/env python3
"""Create public synthetic values for build checks; NEVER install that binary."""
import argparse
import base64
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    values = {
        "wifi_ssid": "HomeKind-build-fixture-only",
        "wifi_password": "not-a-real-network-password",
        "api_encryption_key": base64.b64encode(bytes(range(32))).decode(),
        "fallback_ap_password": "synthetic-build-only",
    }
    fd = os.open(args.directory / "secrets.yaml", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write("# PUBLIC SYNTHETIC BUILD FIXTURE. NEVER INSTALL THIS FIRMWARE.\n")
        for key, value in values.items():
            stream.write(f"{key}: {json.dumps(value)}\n")
    print("Synthetic build values created. Do not flash the resulting binary.")


if __name__ == "__main__":
    main()
