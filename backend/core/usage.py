"""In-process usage counters: LLM tokens, embedding calls, speech seconds.

Feeds the free-tier watch (GET /metrics) and the cost-per-1,000-queries figure from the load test.
Prices are per 1M tokens in USD and are placeholders: update them from the provider's price page on Day 6.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Dict

PRICES_PER_M_TOKENS = {
    "llm_in": 0.30,    # Gemini Flash paid-tier input price, USD / 1M tokens (free tier = 0)
    "llm_out": 2.50,   # output
    "embed_in": 0.15,  # embeddings
}

_lock = threading.Lock()
_counters: Dict[str, float] = defaultdict(float)


def record_usage(kind: str, tokens_in: int = 0, tokens_out: int = 0, calls: int = 1) -> None:
    with _lock:
        _counters[f"{kind}_calls"] += calls
        _counters[f"{kind}_in"] += tokens_in
        _counters[f"{kind}_out"] += tokens_out


def record_query() -> None:
    with _lock:
        _counters["queries"] += 1


def snapshot() -> Dict[str, float]:
    with _lock:
        data = dict(_counters)
    q = max(data.get("queries", 0), 1)
    cost = (data.get("llm_in", 0) * PRICES_PER_M_TOKENS["llm_in"]
            + data.get("llm_out", 0) * PRICES_PER_M_TOKENS["llm_out"]
            + data.get("embed_in", 0) * PRICES_PER_M_TOKENS["embed_in"]) / 1_000_000
    data["est_cost_usd_total_if_paid"] = round(cost, 6)
    data["est_cost_usd_per_1000_queries_if_paid"] = round(cost / q * 1000, 4)
    return data


def reset() -> None:
    with _lock:
        _counters.clear()
