"""bge-reranker-v2-m3 cross-encoder as a tiny HTTP service for a free Hugging Face Space (CPU).

POST /rerank {"query": str, "documents": [str]} -> {"scores": [float in 0..1], "model": str}
Optional auth: set the Space secret RERANKER_TOKEN and send "Authorization: Bearer <token>".
"""
import math
import os
import time
from typing import List

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sentence_transformers import CrossEncoder

MODEL = os.environ.get("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
TOKEN = os.environ.get("RERANKER_TOKEN", "")
MAX_DOCS = 50

app = FastAPI(title="Haqdar reranker")
# max_length 512 keeps CPU latency for 20 pairs around 1-3 s on the free Space hardware.
model = CrossEncoder(MODEL, max_length=512, device="cpu")


class RerankRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    documents: List[str] = Field(..., min_length=1, max_length=MAX_DOCS)


@app.get("/")
def root():
    return {"status": "ok", "model": MODEL}


@app.post("/rerank")
def rerank(req: RerankRequest, authorization: str = Header("")):
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "bad token")
    t0 = time.perf_counter()
    raw = model.predict([(req.query, d[:2000]) for d in req.documents], batch_size=16)
    # Single-logit cross-encoders may return raw logits depending on the library version: squash to 0..1.
    scores = [float(s) for s in raw]
    if scores and (min(scores) < 0 or max(scores) > 1):
        scores = [1 / (1 + math.exp(-s)) for s in scores]
    return {"scores": scores, "model": MODEL, "ms": round((time.perf_counter() - t0) * 1000, 1)}
