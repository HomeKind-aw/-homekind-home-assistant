"""One-time pairing initiated explicitly from Belmelo's microphone settings."""
import secrets
try:
    import probatio as vol
except ImportError:  # Home Assistant releases before the validation rename.
    import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.helpers import entity_registry as er, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .client import pair, VoiceConnectionError
from .const import DOMAIN

class BelleConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            registry = er.async_get(self.hass)
            satellites = [
                entry for entry in er.async_entries_for_device(registry, user_input["microphone"])
                if entry.domain == "assist_satellite" and entry.platform == "esphome" and not entry.disabled_by
            ]
            if len(satellites) != 1:
                return self._show_pairing({"microphone": "choose_voice_satellite"})
            if not hasattr(self, "_pair_token"):
                self._pair_token = secrets.token_hex(32)
            try:
                result = await pair(async_get_clientsession(self.hass), user_input["pairing_code"], self._pair_token)
                await self.async_set_unique_id(result["endpoint_id"])
                data = {"endpoint_id":result["endpoint_id"], "name":result["name"],
                    "room_id":result["room_id"], "token":self._pair_token, "microphone":user_input["microphone"]}
                for entry in self._async_current_entries():
                    if entry.unique_id == result["endpoint_id"]:
                        self.hass.config_entries.async_update_entry(entry, data={**entry.data, **data})
                        await self.hass.config_entries.async_reload(entry.entry_id)
                        return self.async_abort(reason="reconfigured")
                return self.async_create_entry(title=result["name"], data=data)
            except VoiceConnectionError:
                errors["base"] = "cannot_connect"
        return self._show_pairing(errors)

    def _show_pairing(self, errors):
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("pairing_code"):str,
            vol.Required("microphone"):selector.DeviceSelector(selector.DeviceSelectorConfig(entity={"integration": "esphome", "domain": "assist_satellite"})),
        }), errors=errors)
