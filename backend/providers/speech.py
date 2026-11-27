"""Speech provider interface for /voice. Voice UX is Person B's lane; this is the backend seam.

`SarvamSpeech` targets Sarvam AI's REST API (speech-to-text + text-to-speech, Indian languages).
Check request/response field names against Sarvam's current docs when wiring the real key on Day 5.
With no SARVAM_API_KEY set, `get_speech()` returns None and /voice answers 503.
"""
from __future__ import annotations

import base64
import os
from typing import Optional, Tuple

import httpx

SARVAM_BASE = "https://api.sarvam.ai"
LANG_CODES = {"hi": "hi-IN", "hinglish": "hi-IN", "en": "en-IN"}


class Speech:
    def transcribe(self, audio: bytes, filename: str, content_type: str) -> Tuple[str, Optional[str]]:
        """Returns (transcript, detected language code)."""
        raise NotImplementedError

    def synthesize(self, text: str, lang: str) -> Optional[str]:
        """Returns a data: URL with audio, or None."""
        raise NotImplementedError


class SarvamSpeech(Speech):
    def __init__(self, api_key: str) -> None:
        self.headers = {"api-subscription-key": api_key}
        self.client = httpx.Client(timeout=60.0)

    def transcribe(self, audio, filename, content_type):
        r = self.client.post(f"{SARVAM_BASE}/speech-to-text", headers=self.headers,
                             files={"file": (filename, audio, content_type)},
                             data={"model": os.environ.get("SARVAM_STT_MODEL", "saarika:v2.5"),
                                   "language_code": "unknown"})
        r.raise_for_status()
        data = r.json()
        return data.get("transcript", ""), data.get("language_code")

    def synthesize(self, text, lang):
        r = self.client.post(f"{SARVAM_BASE}/text-to-speech", headers=self.headers,
                             json={"text": text[:1500], "target_language_code": LANG_CODES.get(lang, "hi-IN"),
                                   "model": os.environ.get("SARVAM_TTS_MODEL", "bulbul:v2")})
        r.raise_for_status()
        audios = r.json().get("audios") or []
        return f"data:audio/wav;base64,{audios[0]}" if audios else None


def get_speech() -> Optional[Speech]:
    key = os.environ.get("SARVAM_API_KEY", "").strip()
    return SarvamSpeech(key) if key else None


def encode_audio(raw: bytes, mime: str = "audio/wav") -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"
