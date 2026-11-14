"""Bounded, no-retry transport for the approved shared microphone binding."""
from __future__ import annotations
import asyncio
from collections import OrderedDict
import io
import time
import uuid
import wave
from aiohttp import ClientError, ClientTimeout
from .const import API_URL, MAX_PCM, MAX_REPLY

class VoiceConnectionError(Exception):
    """A public, credential-free connection error."""

def make_wave(pcm: bytes) -> bytes:
    """Frame the raw Assist stream for the existing cloud transcription route."""
    if not pcm or len(pcm) > MAX_PCM or len(pcm) % 2:
        raise VoiceConnectionError("Use a short voice request, up to thirty seconds.")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(pcm)
    return buf.getvalue()

async def _request(session, *, data=None, headers=None, audio=None):
    """Never retry a request whose physical or paid outcome may be unknown."""
    try:
        async with session.post(API_URL, json=data if audio is None else None,
            data=audio, headers=headers, timeout=ClientTimeout(total=35),
            allow_redirects=False) as response:
            maximum = MAX_REPLY if response.headers.get("Content-Type", "").startswith("audio/") else 65536
            chunks, size = [], 0
            async for chunk in response.content.iter_chunked(16384):
                size += len(chunk)
                if size > maximum:
                    raise VoiceConnectionError("Belle returned a reply that was too large.")
                chunks.append(chunk)
            body = b"".join(chunks)
            if response.status != 200:
                raise VoiceConnectionError("Belle could not finish the request. Check its microphone access and connection in Belmelo.")
            if response.headers.get("Content-Type", "").startswith("audio/"):
                return body
            import json
            return json.loads(body)
    except (ClientError, asyncio.TimeoutError, ValueError) as err:
        raise VoiceConnectionError("Belle is temporarily unavailable. Check the lights before repeating a control.") from err

async def pair(session, code: str, token: str):
    return await _request(session, data={"operation":"pair", "pairing_code":code.strip().lower(), "token":token})

class BelleClient:
    def __init__(self, session, data):
        self.session = session
        self.endpoint = data["endpoint_id"]
        self.token = data["token"]
        self.microphone = data["microphone"]
        self.name = data["name"]
        self.lock = asyncio.Lock()
        self.pending = None
        self.history = OrderedDict()
        self.replies = OrderedDict()

    def headers(self, turn, *, audio=False):
        return {"Authorization":"Bearer " + self.token,
            "X-Belmelo-Endpoint":self.endpoint, "X-Belmelo-Turn":turn,
            "Content-Type":"audio/wav" if audio else "application/json"}

    async def transcribe(self, stream):
        if self.lock.locked() or self.pending and time.monotonic() - self.pending[2] < 45:
            raise VoiceConnectionError("A voice request is already in progress.")
        async with self.lock:
            chunks = []
            size = 0
            async for chunk in stream:
                size += len(chunk)
                if size > MAX_PCM + 44:
                    raise VoiceConnectionError("Use a shorter voice request.")
                chunks.append(chunk)
            raw = b"".join(chunks)
            # Assist normally delivers PCM. Accept a complete standard WAV too.
            if raw.startswith(b"RIFF") and raw[8:12] == b"WAVE":
                with wave.open(io.BytesIO(raw), "rb") as reader:
                    if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) != (1, 2, 16000):
                        raise VoiceConnectionError("The microphone audio format is unsupported.")
                    raw = reader.readframes(reader.getnframes())
            turn = str(uuid.uuid4())
            value = await _request(self.session, audio=make_wave(raw), headers=self.headers(turn, audio=True))
            if not isinstance(value.get("text"), str) or not value["text"].strip():
                raise VoiceConnectionError("Belle did not catch a request.")
            self.pending = (turn, value["text"], time.monotonic())
            return value["text"]

    async def converse(self, text, conversation_id, device_id):
        if device_id is not None and device_id != self.microphone:
            raise VoiceConnectionError("Choose the Belle pipeline paired to this microphone.")
        if self.lock.locked():
            raise VoiceConnectionError("A voice request is already in progress.")
        async with self.lock:
            turn = self.pending[0] if self.pending and self.pending[1] == text and time.monotonic() - self.pending[2] < 45 else str(uuid.uuid4())
            self.pending = None
            previous = self.history.get(conversation_id)
            body = {"operation":"converse", "text":text}
            if previous and time.monotonic() - previous[1] < 280:
                body["previous_turn_id"] = previous[0]
            value = await _request(self.session, data=body, headers=self.headers(turn))
            if not isinstance(value.get("text"), str) or not value["text"]:
                raise VoiceConnectionError("Belle returned an empty reply.")
            self.history[conversation_id] = (turn, time.monotonic())
            self.replies[value["text"]] = (turn, time.monotonic())
            while len(self.history) > 16:
                self.history.popitem(last=False)
            while len(self.replies) > 16:
                self.replies.popitem(last=False)
            return value

    async def speak(self, message):
        item = self.replies.get(message)
        if item is None or time.monotonic() - item[1] > 280:
            raise VoiceConnectionError("Choose a current Belle reply.")
        return await _request(self.session, data={"operation":"speak", "text":message}, headers=self.headers(item[0]))
