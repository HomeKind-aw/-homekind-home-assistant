"""Set up Belle engines and a dedicated pipeline without local-intent bypass."""
import logging
from homeassistant.components import assist_pipeline
from homeassistant.const import Platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .client import BelleClient
from .const import DOMAIN
from .satellite import SatelliteConnection

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.STT, Platform.CONVERSATION, Platform.TTS]

async def async_setup_entry(hass, entry):
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = BelleClient(async_get_clientsession(hass), entry.data)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    client = hass.data[DOMAIN][entry.entry_id]
    connection = SatelliteConnection(hass, entry, client)
    hass.data.setdefault(DOMAIN + "_satellites", {})[entry.entry_id] = connection
    connection.start()
    registry = er.async_get(hass)
    engine = lambda kind: registry.async_get_entity_id(kind, DOMAIN, entry.data["endpoint_id"] + "_" + kind)
    stt_id, conversation_id, tts_id = (engine(kind) for kind in ("stt", "conversation", "tts"))
    pipeline = next((p for p in assist_pipeline.async_get_pipelines(hass) if p.id == entry.data.get("pipeline_id")), None)
    try:
        if stt_id and conversation_id and tts_id:
            new_pipeline = pipeline is None
            if pipeline is None:
                pipeline = await assist_pipeline.async_create_default_pipeline(hass, stt_id, tts_id, "Belle · " + entry.title)
            if pipeline is not None:
                await assist_pipeline.async_update_pipeline(hass, pipeline,
                    conversation_engine=conversation_id, conversation_language="en", language="en",
                    stt_engine=stt_id, stt_language="en", tts_engine=tts_id, tts_language="en",
                    prefer_local_intents=False)
                hass.config_entries.async_update_entry(entry, data={**entry.data, "pipeline_id":pipeline.id})
                name = pipeline.name
                candidates = [r for r in er.async_entries_for_device(registry, entry.data["microphone"])
                    if r.domain == "select" and (s := hass.states.get(r.entity_id))
                    and name in s.attributes.get("options", [])]
                if len(candidates) == 1 and (new_pipeline or hass.states.get(candidates[0].entity_id).state == name):
                    await hass.services.async_call("select", "select_option",
                        {"entity_id":candidates[0].entity_id, "option":name}, blocking=True)
                    return True
    except Exception:
        # The engines are still usable. Never select a different room/device or
        # silently fall back to the unrestricted built-in conversation agent.
        _LOGGER.warning("Belle engines loaded; select the dedicated Belle assistant on the chosen microphone")
    from homeassistant.components import persistent_notification
    persistent_notification.async_create(hass,
        "Open Settings → Voice assistants and check Belle · " + entry.title +
        ". Select it as the assistant on your chosen microphone. All three engines should be Belmelo Voice; local command handling must be off.",
        title="Finish Belle microphone setup", notification_id=DOMAIN + "_" + entry.entry_id)
    return True

async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        connection = hass.data.get(DOMAIN + "_satellites", {}).pop(entry.entry_id, None)
        if connection:
            await connection.close()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        return True
    return False
