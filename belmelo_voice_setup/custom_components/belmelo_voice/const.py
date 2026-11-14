"""Belmelo room voice protocol. Credentials never leave Home Assistant except TLS."""
DOMAIN = "belmelo_voice"
API_URL = "https://app.belmelo.com/api/room-voice"
LANGUAGES = ["en", "en-AU", "en-GB", "en-US"]
MAX_PCM = 960000
MAX_REPLY = 2000000
