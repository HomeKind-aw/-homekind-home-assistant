"""Confirm a selected native HA network discovery. Never provision Wi-Fi or relay forms."""
import json
import re
import time
from urllib.parse import quote

from bridge import BridgeError, CLOUD_URL, VERSION, atomic_json, utc_timestamp
from device_onboarding import LocalHome, discovery_metadata, uuid_ok, NETWORK_DISCOVERY_SOURCES

OUTCOMES = {"connected", "approval_required", "not_found", "unavailable", "uncertain"}


class NetworkHome(LocalHome):
    def locate(self, key):
        with self.socket() as ws:
            self.auth(ws)
            flows = self.query(ws, 1, "config_entries/flow/progress")
        if not isinstance(flows, list) or len(flows) > 1000:
            raise BridgeError("Network discovery is unavailable.")
        for flow in flows:
            metadata = discovery_metadata([flow])
            if not metadata or metadata[0]["key"] != key:
                continue
            if flow.get("context", {}).get("source") not in NETWORK_DISCOVERY_SOURCES:
                return None
            ident = flow.get("flow_id")
            if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", ident):
                return None
            details = self.c.ha("config/config_entries/flow/" + quote(ident, safe=""), timeout=8)
            if not isinstance(details, dict) or details.get("flow_id") != ident or details.get("handler") != flow.get("handler"):
                return None
            return details
        return None

    @staticmethod
    def confirm_only(flow):
        if not isinstance(flow, dict) or flow.get("type") != "form" or flow.get("errors"):
            return False
        if "data_schema" not in flow or flow["data_schema"] not in (None, []):
            return False
        step = flow.get("step_id")
        if not isinstance(step, str) or not re.fullmatch(r"[a-z0-9_]{1,80}", step):
            return False
        if step not in {"confirm", "confirm_discovery", "discovery_confirm"} and flow.get("last_step") is not True:
            return False
        # QR, account, keys and external authorisation always need a person.
        placeholders = flow.get("description_placeholders") or {}
        if not isinstance(placeholders, dict):
            return False
        return not any(re.search(r"qr|url|link|token|password|credential|auth|account|(?:^|_)key(?:_|$)|(?:^|_)code(?:_|$)", str(k), re.I) for k in placeholders)

    def confirm(self, flow):
        # The sole write is an empty confirmation to an existing local flow.
        result = self.c.ha("config/config_entries/flow/" + quote(flow["flow_id"], safe=""), {}, timeout=12)
        if isinstance(result, dict) and (result.get("type") == "create_entry" or result.get("type") == "abort" and result.get("reason") in {"already_configured", "single_instance_allowed"}):
            return "connected"
        return "approval_required"


class NetworkSetup:
    def __init__(self, manager, home_factory=NetworkHome, clock=time.time):
        self.manager, self.home_factory, self.clock = manager, home_factory, clock
        self.path = manager.directory / "network_setup.json"
        if self.path.exists() and self.path.stat().st_size > 65536:
            raise BridgeError("Check local network setup status.")
        self.saved = json.loads(self.path.read_text()) if self.path.exists() else {}
        if not isinstance(self.saved, dict):
            raise BridgeError("Check local network setup status.")

    def save(self):
        atomic_json(self.path, self.saved)

    def current(self, options):
        with self.manager.lock:
            now = self.manager.options()
            return bool(self.manager.paired() and now.get("managed_settings") and now.get("management_id") == options.get("management_id"))

    def call(self, client, options, op, **payload):
        return self.manager.request(CLOUD_URL + "/network-setup", client.credentials["token"], {
            "bridge_id": client.credentials["bridge_id"], "operation": op,
            "payload": {"protocol": 1, "management_id": options.get("management_id", ""), **payload}
        }, timeout=5)

    def receipt(self, client, options):
        status = self.saved.get("status")
        if status not in OUTCOMES:
            return
        result = self.call(client, options, "ack", request_id=self.saved["id"], status=status)
        if isinstance(result, dict) and result.get("accepted") is True:
            self.saved["acknowledged"] = True
            self.save()

    def step(self, client, options):
        if not self.current(options):
            return
        # Called only while a person has an active existing-device search.
        # There is no additional household-wide idle polling worker.
        result = self.call(client, options, "poll", version=VERSION, available=True)
        directive = result.get("directive") if isinstance(result, dict) else None
        if not isinstance(directive, dict):
            if self.saved.get("management_id") == options.get("management_id") and not self.saved.get("acknowledged"):
                self.receipt(client, options)
            return
        ident, key = directive.get("id"), directive.get("key")
        if not uuid_ok(ident) or not uuid_ok(directive.get("session_id")) or not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
            return
        deadline = utc_timestamp(directive.get("expires_at"))
        if directive.get("management_id") != options.get("management_id") or not self.clock() < deadline <= self.clock() + 130:
            return
        if self.saved.get("id") == ident:
            if not self.saved.get("status"):
                self.saved["status"] = "uncertain"  # Crash/timeout: never repeat a dispatched confirmation.
                self.save()
            self.receipt(client, options)
            return
        self.saved = {"id": ident, "management_id": options["management_id"], "expires_at": deadline}
        self.save()
        if directive.get("status") != "queued":
            self.saved["status"] = "uncertain"
        else:
            home = self.home_factory(client)
            try:
                flow = home.locate(key)
                if not flow:
                    self.saved["status"] = "not_found"
                elif not home.confirm_only(flow):
                    self.saved["status"] = "approval_required"
                elif self.current(options) and self.clock() < deadline:
                    self.saved["stage"] = "authorizing"
                    self.save()
                    authorised = self.call(client, options, "authorize", request_id=ident)
                    if not isinstance(authorised, dict) or authorised.get("authorised") is not True:
                        self.saved["status"] = "unavailable"
                        self.save()
                        self.receipt(client, options)
                        return
                    if not self.current(options) or self.clock() >= deadline:
                        return
                    self.saved["stage"] = "dispatched"
                    self.save()  # Persist before the single HA configuration write.
                    try:
                        self.saved["status"] = home.confirm(flow)
                    except Exception:
                        self.saved["status"] = "uncertain"
            except Exception:
                if self.saved.get("stage") == "authorizing":
                    self.saved["status"] = "uncertain"
                else:
                    self.saved["status"] = "unavailable"
        self.save()
        self.receipt(client, options)
