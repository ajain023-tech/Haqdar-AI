"""Index one scheme: upsert the row, and (only if its content changed) re-chunk and re-embed it.

This is the incremental re-index hook the daily refresh calls: unchanged content_hash => no embedding cost.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from backend.providers.embeddings import Embedder, EmbeddingQuotaExceeded, get_embedder
from backend.rag.chunking import Chunk, chunk_scheme
from backend.rag.store import Store, get_store


def content_hash(sections_by_lang: Dict[str, Dict[str, str]]) -> str:
    """Stable hash of the cleaned section text (whitespace-normalised) across languages."""
    norm = {lang: {k: re.sub(r"\s+", " ", v or "").strip() for k, v in sorted(secs.items())}
            for lang, secs in sorted(sections_by_lang.items())}
    return hashlib.sha256(json.dumps(norm, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def build_chunks(scheme: Dict[str, Any], sections_by_lang: Dict[str, Dict[str, str]]) -> List[Chunk]:
    chunks: List[Chunk] = []
    for lang, sections in sections_by_lang.items():
        name = scheme.get("name_hi") if lang == "hi" and scheme.get("name_hi") else scheme["name"]
        chunks.extend(chunk_scheme(name, sections, lang=lang))
    return chunks


def index_scheme(scheme: Dict[str, Any], sections_by_lang: Dict[str, Dict[str, str]],
                 store: Optional[Store] = None, embedder: Optional[Embedder] = None,
                 force: bool = False) -> str:
    """Returns 'added' | 'updated' | 'unchanged'."""
    store = store or get_store()
    embedder = embedder or get_embedder()
    scheme = dict(scheme)
    scheme["content_hash"] = scheme.get("content_hash") or content_hash(sections_by_lang)
    raw = json.dumps(sections_by_lang, ensure_ascii=False)
    existing = store.get_scheme(scheme["slug"])
    if existing and existing.get("content_hash") == scheme["content_hash"] and not force:
        sid, status = store.upsert_scheme(scheme, raw_text=raw)  # only moves last_checked
        return status
    # Embed BEFORE writing the row: if embedding fails (rate limit, outage), nothing is saved and the
    # next run retries this scheme instead of seeing an "unchanged" hash with no chunks behind it.
    chunks = build_chunks(scheme, sections_by_lang)
    try:
        embeddings = embedder.embed_documents([c.text for c in chunks]) if chunks else []
    except EmbeddingQuotaExceeded:
        # Out of embedding quota: save the text now (keyword search works at once) and let
        # scripts/backfill_embeddings.py add the vectors when quota is back.
        embeddings = [None] * len(chunks)
    sid, status = store.upsert_scheme(scheme, raw_text=raw)
    store.replace_chunks(sid, chunks, embeddings)
    return "updated" if status == "unchanged" else status
