"""Only speak the current reply authorised for this room binding."""
from homeassistant.components import tts
from .client import VoiceConnectionError
from .const import DOMAIN, LANGUAGES
from .entity import BelleEntity

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([BelleTTS(hass.data[DOMAIN][entry.entry_id])])

class BelleTTS(BelleEntity, tts.TextToSpeechEntity):
    _attr_default_language = "en"
    _attr_supported_languages = LANGUAGES

    def __init__(self, client):
        super().__init__(client, "tts", "Spoken replies")

    async def async_get_tts_audio(self, message, language, options=None):
        try:
            return ("mp3", await self.client.speak(message))
        except VoiceConnectionError:
            return (None, None)
