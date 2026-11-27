"""LLM provider interface. Gemini Flash by default; swap via LLM_PROVIDER without touching callers.

Callers depend only on `LLM.complete` / `LLM.stream` / `LLM.complete_json`. `FakeLLM` lets the whole
pipeline run offline (tests, CI eval): callers check `llm.is_fake` and use their deterministic fallback.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterator, List, Optional

import httpx

from backend.core.config import Settings, get_settings
from backend.core.usage import record_usage

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


class LLM:
    name = "base"
    is_fake = False

    def complete(self, prompt: str, system: Optional[str] = None, temperature: float = 0.2,
                 json_mode: bool = False) -> str:
        raise NotImplementedError

    def stream(self, prompt: str, system: Optional[str] = None, temperature: float = 0.2) -> Iterator[str]:
        yield self.complete(prompt, system=system, temperature=temperature)

    def complete_json(self, prompt: str, system: Optional[str] = None) -> Any:
        return parse_json(self.complete(prompt, system=system, temperature=0.0, json_mode=True))


def parse_json(text: str) -> Any:
    """Parse JSON from a model reply, tolerating ```json fences and leading prose."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    return None


class GeminiLLM(LLM):
    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout: float = 60.0):
        self.api_key = api_key
        self.model = model
        self.client = httpx.Client(timeout=timeout)

    def _body(self, prompt: str, system: Optional[str], temperature: float, json_mode: bool) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        return body

    @staticmethod
    def _text(payload: Dict[str, Any]) -> str:
        parts: List[str] = []
        for cand in payload.get("candidates", [])[:1]:
            for part in cand.get("content", {}).get("parts", []):
                parts.append(part.get("text", ""))
        return "".join(parts)

    def _usage(self, payload: Dict[str, Any]) -> None:
        meta = payload.get("usageMetadata") or {}
        record_usage("llm", tokens_in=meta.get("promptTokenCount", 0),
                     tokens_out=meta.get("candidatesTokenCount", 0))

    def complete(self, prompt, system=None, temperature=0.2, json_mode=False) -> str:
        url = f"{GEMINI_BASE}/models/{self.model}:generateContent"
        r = self.client.post(url, params={"key": self.api_key},
                             json=self._body(prompt, system, temperature, json_mode))
        r.raise_for_status()
        payload = r.json()
        self._usage(payload)
        return self._text(payload)

    def stream(self, prompt, system=None, temperature=0.2) -> Iterator[str]:
        url = f"{GEMINI_BASE}/models/{self.model}:streamGenerateContent"
        with self.client.stream("POST", url, params={"key": self.api_key, "alt": "sse"},
                                json=self._body(prompt, system, temperature, False)) as r:
            r.raise_for_status()
            last: Dict[str, Any] = {}
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    last = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                piece = self._text(last)
                if piece:
                    yield piece
            if last:
                self._usage(last)


class FakeLLM(LLM):
    """Offline stand-in. Returns empty strings; every caller has a deterministic fallback."""
    name = "fake"
    is_fake = True

    def complete(self, prompt, system=None, temperature=0.2, json_mode=False) -> str:
        return ""


_llm: Optional[LLM] = None


def get_llm(settings: Optional[Settings] = None) -> LLM:
    global _llm
    if _llm is None:
        s = settings or get_settings()
        if s.llm_provider == "gemini" and s.gemini_api_key:
            _llm = GeminiLLM(s.gemini_api_key, s.llm_model)
        else:
            _llm = FakeLLM()
    return _llm


def set_llm(llm: Optional[LLM]) -> None:
    """Override the process-wide LLM (tests, eval)."""
    global _llm
    _llm = llm
