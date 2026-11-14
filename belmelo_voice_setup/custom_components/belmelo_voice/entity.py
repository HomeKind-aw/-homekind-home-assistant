"""Common metadata for the three engines of one room binding."""
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN

class BelleEntity:
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, client, kind, name):
        self.client = client
        self._attr_unique_id = client.endpoint + "_" + kind
        self._attr_name = name
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, client.endpoint)},
            name=client.name, manufacturer="Belmelo", model="Shared room voice")
