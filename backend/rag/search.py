"""Retrieval: vector, BM25, hybrid (reciprocal rank fusion) and hybrid + rerank.

`retrieve(query, mode=...)` is the single entry point used by the agent, /search and the eval.
Modes map 1:1 onto the eval's setup rows: vector | hybrid | rerank.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from backend.providers.embeddings import Embedder, get_embedder
from backend.providers.reranker import Reranker, get_reranker
from backend.rag.store import Filters, Hit, Store, get_store

RRF_K = 60          # standard RRF constant
CANDIDATES = 20     # per-retriever depth, and how many go to the reranker
TOP_K = 5


@dataclass
class RetrievalResult:
    hits: List[Hit]
    mode: str
    top_score: float = 0.0           # reranker score of hits[0] (or fused score when no rerank)
    timings_ms: Dict[str, float] = field(default_factory=dict)
    reranker: str = ""

    def to_dict(self) -> Dict:
        return {"mode": self.mode, "top_score": self.top_score, "timings_ms": self.timings_ms,
                "reranker": self.reranker, "hits": [h.to_dict() for h in self.hits]}


def reciprocal_rank_fusion(*ranked_lists: List[Hit], k: int = RRF_K) -> List[Hit]:
    """Merge ranked lists: score(d) = sum over lists of 1 / (k + rank). Keeps the first Hit seen per chunk."""
    fused: Dict[int, Hit] = {}
    scores: Dict[int, float] = {}
    for name_idx, ranked in enumerate(ranked_lists):
        for rank, hit in enumerate(ranked, start=1):
            if hit.chunk_id not in fused:
                fused[hit.chunk_id] = Hit(**{**hit.to_dict(), "scores": dict(hit.scores)})
            fused[hit.chunk_id].scores[f"list{name_idx}"] = hit.score
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
    out = sorted(fused.values(), key=lambda h: scores[h.chunk_id], reverse=True)
    for h in out:
        h.scores["rrf"] = scores[h.chunk_id]
        h.score = scores[h.chunk_id]
    return out


def _dedupe_per_scheme(hits: List[Hit], max_per_scheme: int = 2) -> List[Hit]:
    """Keep answers diverse: at most N chunks from the same scheme in the final top-k."""
    seen: Dict[int, int] = {}
    out = []
    for h in hits:
        if seen.get(h.scheme_id, 0) >= max_per_scheme:
            continue
        seen[h.scheme_id] = seen.get(h.scheme_id, 0) + 1
        out.append(h)
    return out


def retrieve(query: str, mode: str = "rerank", filters: Optional[Filters] = None, top_k: int = TOP_K,
             store: Optional[Store] = None, embedder: Optional[Embedder] = None,
             reranker: Optional[Reranker] = None, alt_query: Optional[str] = None) -> RetrievalResult:
    """`alt_query` = English rewrite of a Hindi/Hinglish question; both are searched and fused."""
    store = store or get_store()
    embedder = embedder or get_embedder()
    filters = filters or Filters()
    timings: Dict[str, float] = {}
    queries = [query] + ([alt_query] if alt_query and alt_query.strip() != query.strip() else [])

    t0 = time.perf_counter()
    qvecs = [embedder.embed_query(q) for q in queries]
    timings["embed"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    vec_lists = [store.vector_search(v, CANDIDATES, filters) for v in qvecs]
    timings["vector"] = (time.perf_counter() - t0) * 1000
    for lst in vec_lists:
        for h in lst:
            h.scores["vector"] = h.score

    if mode == "vector":
        merged = vec_lists[0] if len(vec_lists) == 1 else reciprocal_rank_fusion(*vec_lists)
        hits = _dedupe_per_scheme(merged)[:top_k]
        top = hits[0].scores.get("vector", hits[0].score) if hits else 0.0
        return RetrievalResult(hits, mode, top, timings)

    t0 = time.perf_counter()
    kw_lists = [store.keyword_search(q, CANDIDATES, filters) for q in queries]
    timings["bm25"] = (time.perf_counter() - t0) * 1000
    for lst in kw_lists:
        for h in lst:
            h.scores["bm25"] = h.score

    fused = reciprocal_rank_fusion(*vec_lists, *kw_lists)
    if mode == "hybrid":
        hits = _dedupe_per_scheme(fused)[:top_k]
        # RRF scores are rank-based; map the top one onto [0, 1] relative to a hit ranked #1 everywhere.
        best = len(vec_lists + kw_lists) / (RRF_K + 1)
        top = (hits[0].score / best) if hits else 0.0
        return RetrievalResult(hits, mode, top, timings)

    reranker = reranker or get_reranker()
    candidates = fused[:CANDIDATES]
    rr_query = query if not alt_query else f"{query} ({alt_query})"
    t0 = time.perf_counter()
    rr_scores = reranker.score(rr_query, [h.text for h in candidates])
    timings["rerank"] = (time.perf_counter() - t0) * 1000
    for h, s in zip(candidates, rr_scores):
        h.scores["rerank"] = s
        h.score = s
    candidates.sort(key=lambda h: h.score, reverse=True)
    hits = _dedupe_per_scheme(candidates)[:top_k]
    return RetrievalResult(hits, mode, hits[0].score if hits else 0.0, timings,
                           reranker=getattr(reranker, "last_used", reranker.name))
