"""Belle's permission-checked shared room conversation, with bounded follow-ups."""
import uuid
from homeassistant.components.conversation import ConversationEntity, ConversationEntityFeature, ConversationResult
from homeassistant.helpers import intent
from .client import VoiceConnectionError
from .const import DOMAIN, LANGUAGES
from .entity import BelleEntity

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([BelleConversation(hass.data[DOMAIN][entry.entry_id])])

class BelleConversation(BelleEntity, ConversationEntity):
    _attr_supported_features = ConversationEntityFeature.CONTROL

    def __init__(self, client):
        super().__init__(client, "conversation", "Belle")

    @property
    def supported_languages(self):
        return LANGUAGES

    async def _async_handle_message(self, user_input, chat_log):
        ident = user_input.conversation_id or str(uuid.uuid4())
        response = intent.IntentResponse(language=user_input.language)
        continuing = False
        try:
            value = await self.client.converse(user_input.text, ident, user_input.device_id)
            if value.get("silent") is not True:
                response.async_set_speech(value["text"])
            continuing = value.get("continue_conversation") is True
        except VoiceConnectionError as err:
            response.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE, str(err))
        return ConversationResult(response=response, conversation_id=ident, continue_conversation=continuing)
