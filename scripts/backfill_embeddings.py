"""Add vectors to chunks that were saved while the embedding quota was used up.

    python -m scripts.backfill_embeddings            # until done or the daily quota runs out
    python -m scripts.backfill_embeddings --max 300  # at most 300 chunks this run

Safe to run any time (also nightly): it only touches chunks whose embedding is NULL.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.providers.embeddings import EmbeddingQuotaExceeded, get_embedder  # noqa: E402
from backend.rag.store import get_store  # noqa: E402


def backfill(max_chunks: int = 0, batch: int = 50, log=print) -> int:
    store, embedder = get_store(), get_embedder()
    done = 0
    while not max_chunks or done < max_chunks:
        rows = store.chunks_without_embeddings(batch if not max_chunks else min(batch, max_chunks - done))
        if not rows:
            break
        try:
            vecs = embedder.embed_documents([r["text"] for r in rows])
        except EmbeddingQuotaExceeded as exc:
            log(f"stopping: {exc}")
            break
        store.set_embeddings([(r["id"], v) for r, v in zip(rows, vecs)])
        done += len(rows)
        log(f"  embedded {done} chunks")
    left = len(store.chunks_without_embeddings(10000))
    log(f"backfill: {done} embedded this run, {left} still without vectors")
    return done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0)
    args = ap.parse_args()
    backfill(args.max)


if __name__ == "__main__":
    main()
