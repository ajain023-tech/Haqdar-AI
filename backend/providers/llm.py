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


class RateLimited(Exception):
    """The provider refused the call for quota reasons (HTTP 429) or timed out."""

    def __init__(self, message: str, retry_after: float = 0.0):
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after(r: httpx.Response) -> float:
    try:
        for det in r.json().get("error", {}).get("details", []):
            if "retryDelay" in det:
                return float(str(det["retryDelay"]).rstrip("s")) + 1
    except Exception:  # noqa: BLE001
        pass
    return 30.0


class GeminiLLM(LLM):
    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout: float = 25.0):
        self.api_key = api_key
        self.model = model
        # Key in a header, never in the URL, so it can't leak into logs or error messages.
        self.client = httpx.Client(timeout=timeout, headers={"x-goog-api-key": api_key})

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
        try:
            r = self.client.post(url, json=self._body(prompt, system, temperature, json_mode))
        except httpx.TimeoutException as exc:
            raise RateLimited(f"{self.model} timed out") from exc
        if r.status_code in (429, 503):
            raise RateLimited(f"{self.model} returned {r.status_code}", _retry_after(r))
        r.raise_for_status()
        payload = r.json()
        self._usage(payload)
        return self._text(payload)

    def stream(self, prompt, system=None, temperature=0.2) -> Iterator[str]:
        url = f"{GEMINI_BASE}/models/{self.model}:streamGenerateContent"
        try:
            ctx = self.client.stream("POST", url, params={"alt": "sse"},
                                     json=self._body(prompt, system, temperature, False))
            r = ctx.__enter__()
        except httpx.TimeoutException as exc:
            raise RateLimited(f"{self.model} timed out") from exc
        try:
            if r.status_code in (429, 503):
                r.read()
                raise RateLimited(f"{self.model} returned {r.status_code}", _retry_after(r))
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
        finally:
            ctx.__exit__(None, None, None)


class FallbackLLM(LLM):
    """Try each model in turn; a rate-limited or timed-out model hands the call to the next one.

    Free-tier quotas are per model, so spreading calls over two models roughly adds their limits.
    With wait=True (batch jobs: eval, ingestion) it sleeps out the rate limit instead of failing.
    """

    def __init__(self, chain: List[LLM], wait: bool = False) -> None:
        self.chain = chain
        self.wait = wait
        self.name = "+".join(getattr(m, "model", m.name) for m in chain)
        self._memo: dict = {}  # deterministic (temperature 0) answers: profile, rewrite, routing, judging

    def complete(self, prompt, system=None, temperature=0.2, json_mode=False) -> str:
        key = (prompt, system, json_mode) if temperature == 0 else None
        if key is not None and key in self._memo:
            return self._memo[key]
        out = self._complete(prompt, system, temperature, json_mode)
        if key is not None:
            if len(self._memo) > 2000:
                self._memo.clear()
            self._memo[key] = out
        return out

    def _complete(self, prompt, system, temperature, json_mode) -> str:
        import time
        for _ in range(6 if self.wait else 1):
            delay = 0.0
            for m in self.chain:
                try:
                    return m.complete(prompt, system=system, temperature=temperature, json_mode=json_mode)
                except RateLimited as exc:
                    delay = max(delay, exc.retry_after)
            if not self.wait:
                break
            time.sleep(min(max(delay, 5.0), 65.0))
        raise RateLimited("all models rate-limited")

    def stream(self, prompt, system=None, temperature=0.2) -> Iterator[str]:
        last_exc: Optional[Exception] = None
        for m in self.chain:
            started = False
            try:
                for piece in m.stream(prompt, system=system, temperature=temperature):
                    started = True
                    yield piece
                return
            except RateLimited as exc:
                if started:
                    raise
                last_exc = exc
        if self.wait:
            yield self.complete(prompt, system=system, temperature=temperature)
            return
        raise last_exc or RateLimited("all models rate-limited")


class FakeLLM(LLM):
    """Offline stand-in. Returns empty strings; every caller has a deterministic fallback."""
    name = "fake"
    is_fake = True

    def complete(self, prompt, system=None, temperature=0.2, json_mode=False) -> str:
        return ""


_llm: Optional[LLM] = None
_fast_llm: Optional[LLM] = None


def _wait_mode() -> bool:
    import os
    return os.environ.get("LLM_WAIT_ON_LIMIT", "0") == "1"


def get_llm(settings: Optional[Settings] = None) -> LLM:
    """The answer-writing model (quality first), falling back to the fast model when rate-limited."""
    global _llm
    if _llm is None:
        s = settings or get_settings()
        if s.llm_provider == "gemini" and s.gemini_api_key:
            chain: List[LLM] = [GeminiLLM(s.gemini_api_key, s.llm_model)]
            if s.llm_fast_model and s.llm_fast_model != s.llm_model:
                chain.append(GeminiLLM(s.gemini_api_key, s.llm_fast_model))
            _llm = FallbackLLM(chain, wait=_wait_mode())
        else:
            _llm = FakeLLM()
    return _llm


def get_fast_llm(settings: Optional[Settings] = None) -> LLM:
    """Model for small structured jobs (profile, rewrite, routing, rerank, self-check)."""
    global _fast_llm
    if _fast_llm is None:
        s = settings or get_settings()
        if s.llm_provider == "gemini" and s.gemini_api_key:
            chain: List[LLM] = [GeminiLLM(s.gemini_api_key, s.llm_fast_model or s.llm_model)]
            if s.llm_fast_model and s.llm_fast_model != s.llm_model:
                chain.append(GeminiLLM(s.gemini_api_key, s.llm_model))
            _fast_llm = FallbackLLM(chain, wait=_wait_mode())
        else:
            _fast_llm = FakeLLM()
    return _fast_llm


def set_llm(llm: Optional[LLM], fast: Optional[LLM] = None) -> None:
    """Override the process-wide LLMs (tests, eval). `fast` defaults to the same object."""
    global _llm, _fast_llm
    _llm = llm
    _fast_llm = fast if fast is not None else llm
