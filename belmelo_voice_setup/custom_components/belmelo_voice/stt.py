"""Broad transcription using the existing Belle speech service."""
from homeassistant.components import stt
from .client import VoiceConnectionError
from .const import DOMAIN, LANGUAGES
from .entity import BelleEntity

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([BelleSTT(hass.data[DOMAIN][entry.entry_id])])

class BelleSTT(BelleEntity, stt.SpeechToTextEntity):
    def __init__(self, client):
        super().__init__(client, "stt", "Speech recognition")

    @property
    def supported_languages(self):
        return LANGUAGES

    @property
    def supported_formats(self):
        return [stt.AudioFormats.WAV]

    @property
    def supported_codecs(self):
        return [stt.AudioCodecs.PCM]

    @property
    def supported_bit_rates(self):
        return [stt.AudioBitRates.BITRATE_16]

    @property
    def supported_sample_rates(self):
        return [stt.AudioSampleRates.SAMPLERATE_16000]

    @property
    def supported_channels(self):
        return [stt.AudioChannels.CHANNEL_MONO]

    async def async_process_audio_stream(self, metadata, stream):
        if not self.check_metadata(metadata):
            return stt.SpeechResult("", stt.SpeechResultState.ERROR)
        try:
            return stt.SpeechResult(await self.client.transcribe(stream), stt.SpeechResultState.SUCCESS)
        except (VoiceConnectionError, OSError, ValueError):
            return stt.SpeechResult("", stt.SpeechResultState.ERROR)
