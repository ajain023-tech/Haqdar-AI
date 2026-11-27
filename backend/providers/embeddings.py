"""Embedding provider interface. Gemini embeddings by default; `HashEmbedder` for offline runs.

Swap to bge-m3 on a Hugging Face Space by adding a class here and setting EMBED_PROVIDER.
"""
from __future__ import annotations

import hashlib
import math
import re
import time
from typing import List, Optional

import httpx

from backend.core.config import Settings, get_settings
from backend.core.usage import record_usage

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


class EmbeddingQuotaExceeded(Exception):
    """The embedding API refused for quota reasons and waiting isn't worth it (daily cap, or a live query)."""


class Embedder:
    name = "base"
    dim = 768

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]


class GeminiEmbedder(Embedder):
    name = "gemini"
    batch_size = 50   # texts per batchEmbedContents call (API max 100)

    def __init__(self, api_key: str, model: str, dim: int, texts_per_minute: Optional[int] = None):
        import os
        self.api_key = api_key
        self.model = model
        self.dim = dim
        # Free tier counts every text in a batch against ~100/minute: pace below that.
        self.texts_per_minute = texts_per_minute or int(os.environ.get("EMBED_TEXTS_PER_MINUTE", "90"))
        self._sent: List[tuple] = []  # (timestamp, n_texts)
        self.client = httpx.Client(timeout=60.0, headers={"x-goog-api-key": api_key})

    def _batch(self, texts: List[str], task: str, wait: bool = True) -> List[List[float]]:
        url = f"{GEMINI_BASE}/models/{self.model}:batchEmbedContents"
        body = {"requests": [{
            "model": f"models/{self.model}",
            "content": {"parts": [{"text": t[:8000]}]},
            "taskType": task,
            "outputDimensionality": self.dim,
        } for t in texts]}
        for attempt in range(8):
            self._pace(len(texts))
            r = self.client.post(url, json=body)
            if r.status_code == 429:
                delay = _retry_delay(r, default=min(60, 5 * 2 ** attempt))
                # Per-minute limit: wait it out (batch jobs only). Daily cap or a live query: give up now.
                if not wait or delay > 90:
                    raise EmbeddingQuotaExceeded(f"embedding quota exceeded (retry in {int(delay)}s)")
                time.sleep(delay)
                continue
            r.raise_for_status()
            record_usage("embed", tokens_in=sum(len(t) // 4 for t in texts), calls=1)
            return [_normalize(e["values"]) for e in r.json()["embeddings"]]
        r.raise_for_status()
        return []

    def _pace(self, n: int) -> None:
        now = time.time()
        self._sent = [(t, k) for t, k in self._sent if now - t < 60]
        used = sum(k for _, k in self._sent)
        if self._sent and used + n > self.texts_per_minute:
            time.sleep(max(0.0, 60 - (now - self._sent[0][0])) + 0.5)
            self._sent = []
        self._sent.append((time.time(), n))

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for i in range(0, len(texts), self.batch_size):
            out.extend(self._batch(texts[i:i + self.batch_size], "RETRIEVAL_DOCUMENT"))
        return out

    def embed_query(self, text: str) -> List[float]:
        return self._batch([text], "RETRIEVAL_QUERY", wait=False)[0]  # a user is waiting: never sleep


_TOKEN = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)


class HashEmbedder(Embedder):
    """Deterministic feature-hashing embedder (words + character trigrams). No network, no model.

    Good enough to exercise the pipeline and CI eval; not a substitute for a real model.
    """
    name = "hash"

    def __init__(self, dim: int = 768):
        self.dim = dim

    def _vec(self, text: str) -> List[float]:
        v = [0.0] * self.dim
        words = [w.lower() for w in _TOKEN.findall(text)]
        feats = list(words)
        for w in words:
            padded = f"#{w}#"
            feats.extend(padded[i:i + 3] for i in range(max(len(padded) - 2, 1)))
        for f in feats:
            h = int(hashlib.md5(f.encode("utf-8")).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 8) & 1 else -1.0
        return _normalize(v)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._vec(t) for t in texts]


def _retry_delay(r: "httpx.Response", default: float) -> float:
    """Seconds from Google's RetryInfo ("46s"), else `default`."""
    try:
        for det in r.json().get("error", {}).get("details", []):
            if "retryDelay" in det:
                return float(str(det["retryDelay"]).rstrip("s")) + 1
    except Exception:  # noqa: BLE001
        pass
    return default


def _normalize(v: List[float]) -> List[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cosine(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


_embedder: Optional[Embedder] = None


def get_embedder(settings: Optional[Settings] = None) -> Embedder:
    global _embedder
    if _embedder is None:
        s = settings or get_settings()
        if s.embed_provider == "gemini" and s.gemini_api_key:
            _embedder = GeminiEmbedder(s.gemini_api_key, s.embed_model, s.embed_dim)
        else:
            _embedder = HashEmbedder(s.embed_dim)
    return _embedder


def set_embedder(e: Optional[Embedder]) -> None:
    global _embedder
    _embedder = e
