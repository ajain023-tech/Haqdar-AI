"""Reranker interface: score (query, passage) pairs in [0, 1].

Primary: bge-reranker-v2-m3 cross-encoder on a free Hugging Face Space (see reranker_space/).
Fallback: Gemini Flash scores the candidates. Last resort (offline): lexical overlap.
A failing primary falls through to the next one so a sleeping Space never breaks a query.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

import httpx

from backend.core.config import Settings, get_settings
from backend.core.lang import STOPWORDS
from backend.providers.llm import LLM, get_fast_llm

log = logging.getLogger(__name__)
_TOKEN = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)


class Reranker:
    name = "base"

    def score(self, query: str, passages: List[str]) -> List[float]:
        raise NotImplementedError


class LexicalReranker(Reranker):
    """Share of query terms present in the passage, squashed to [0, 1]. Offline only."""
    name = "lexical"

    def score(self, query: str, passages: List[str]) -> List[float]:
        q = {t.lower() for t in _TOKEN.findall(query) if len(t) > 2} - STOPWORDS
        if not q:
            return [0.0] * len(passages)
        out = []
        for p in passages:
            words = {t.lower() for t in _TOKEN.findall(p)}
            out.append(len(q & words) / len(q))
        return out


class SpaceReranker(Reranker):
    name = "space"

    def __init__(self, url: str, token: str = "", timeout: float = 20.0):
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = httpx.Client(timeout=timeout)

    def score(self, query: str, passages: List[str]) -> List[float]:
        r = self.client.post(f"{self.url}/rerank", headers=self.headers,
                             json={"query": query, "documents": passages})
        r.raise_for_status()
        return [float(s) for s in r.json()["scores"]]


class LLMReranker(Reranker):
    """Gemini scores each candidate 0-10 for how well it answers the query."""
    name = "llm"

    def __init__(self, llm: LLM):
        self.llm = llm

    def score(self, query: str, passages: List[str]) -> List[float]:
        listing = "\n\n".join(f"[{i}] {p[:700]}" for i, p in enumerate(passages))
        prompt = (
            "Rate how well each passage helps answer the question about Indian government schemes.\n"
            "Return a JSON array of numbers 0-10, one per passage, in order. No other text.\n\n"
            f"Question: {query}\n\nPassages:\n{listing}"
        )
        data = self.llm.complete_json(prompt)
        if not isinstance(data, list) or len(data) != len(passages):
            raise ValueError("LLM reranker returned a malformed score list")
        return [max(0.0, min(1.0, float(x) / 10.0)) for x in data]


class ChainReranker(Reranker):
    def __init__(self, chain: List[Reranker]):
        self.chain = chain
        self.name = "+".join(r.name for r in chain)
        self.last_used = ""

    def score(self, query: str, passages: List[str]) -> List[float]:
        if not passages:
            return []
        for r in self.chain:
            try:
                scores = r.score(query, passages)
                self.last_used = r.name
                return scores
            except Exception as exc:  # noqa: BLE001 - fall through to the next reranker
                log.warning("reranker %s failed: %s", r.name, exc)
        self.last_used = "none"
        return [0.0] * len(passages)


_reranker: Optional[Reranker] = None


def get_reranker(settings: Optional[Settings] = None) -> Reranker:
    global _reranker
    if _reranker is None:
        s = settings or get_settings()
        chain: List[Reranker] = []
        llm = get_fast_llm(s)
        if s.rerank_provider == "space" and s.reranker_url:
            chain.append(SpaceReranker(s.reranker_url, s.reranker_token))
        if s.rerank_provider in ("space", "llm") and not llm.is_fake:
            chain.append(LLMReranker(llm))
        chain.append(LexicalReranker())
        _reranker = ChainReranker(chain)
    return _reranker


def set_reranker(r: Optional[Reranker]) -> None:
    global _reranker
    _reranker = r
