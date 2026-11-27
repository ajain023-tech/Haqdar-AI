"""Run the same 100 questions through four setups and print the metrics table for the slide/README.

    Setup                       mode     rules  self-check
    Vector only                 vector   no     no
    + BM25 hybrid               hybrid   no     no
    + Reranker                  rerank   no     no
    + Rule engine + self-check  rerank   yes    yes

Metrics
    Recall@5      share of questions where a gold scheme's chunk is in the top 5
    Faithfulness  share of answer claims supported by retrieved chunks (same judge for every setup:
                  Gemini when GEMINI_API_KEY is set, lexical otherwise; spot-check by hand)
    Eligibility   rule-engine verdicts vs hand labels in eval/profiles.json (Person B's 10 personas)
    Refusal       trick questions answered "couldn't find" (target 10/10)
    False refus.  real questions wrongly answered "couldn't find" (target 0)
    p95 latency   seconds per question, end to end inside the agent

Usage:
    python -m eval.run_eval                                    # against DATABASE_URL (or empty memory store)
    python -m eval.run_eval --corpus eval/corpus_sample.jsonl  # offline, in-memory corpus (CI)
    python -m eval.run_eval --setups 3,4 --limit 20 --min-recall 0.6
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.agent.flow import Agent, AgentConfig  # noqa: E402
from backend.core import usage  # noqa: E402
from backend.core.cache import MemoryCache  # noqa: E402
from backend.providers.llm import get_llm  # noqa: E402
from backend.rag import selfcheck  # noqa: E402
from backend.rag.indexer import content_hash, index_scheme  # noqa: E402
from backend.rag.store import MemoryStore, Store, get_store, set_store  # noqa: E402
from backend.rules import evaluate  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SETUPS = {
    1: ("Vector only", AgentConfig(mode="vector", use_rules=False, use_selfcheck=False, use_cache=False)),
    2: ("+ BM25 hybrid", AgentConfig(mode="hybrid", use_rules=False, use_selfcheck=False, use_cache=False)),
    3: ("+ Reranker", AgentConfig(mode="rerank", use_rules=False, use_selfcheck=False, use_cache=False)),
    4: ("+ Rule engine + self-check", AgentConfig(mode="rerank", use_rules=True, use_selfcheck=True,
                                                  use_cache=False)),
}


def load_corpus(path: str) -> Store:
    store = MemoryStore()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            scheme = {k: v for k, v in rec["scheme"].items() if k != "tags"}
            scheme["content_hash"] = content_hash(rec["sections"])
            index_scheme(scheme, rec["sections"], store=store)
    return store


def p95(values: List[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]


def run_setup(name: str, cfg: AgentConfig, questions: List[Dict[str, Any]], trick: List[Dict[str, Any]],
              store: Store) -> Dict[str, Any]:
    agent = Agent(cfg, store=store, cache=MemoryCache())
    hits_at_5, faith, latencies, rows = [], [], [], []
    for q in questions:
        t0 = time.perf_counter()
        out = agent.run(q["question"], lang=q["lang"])
        latencies.append(time.perf_counter() - t0)
        top_slugs = [h["slug"] for h in out["retrieval"]["hits"]][:5]
        hit = any(g in top_slugs for g in q["gold_slugs"])
        hits_at_5.append(hit)
        # Uniform faithfulness judge for every setup (the agent's own self-check only runs in setup 4).
        if not out["answer"]["refused"]:
            judged = selfcheck.check(out["answer"]["text"], agent.last_hits)
            faith.append(judged.faithfulness)
        rows.append({"id": q["id"], "lang": q["lang"], "hit": hit, "gold": q["gold_slugs"], "top": top_slugs,
                     "refused": out["answer"]["refused"], "latency_s": round(latencies[-1], 3),
                     "answer": out["answer"]["text"][:300]})

    refusals = []
    for t in trick:
        out = agent.run(t["question"], lang=t["lang"])
        refusals.append(out["answer"]["refused"])
        rows.append({"id": t["id"], "lang": t["lang"], "trick": True, "refused": out["answer"]["refused"],
                     "answer": out["answer"]["text"][:200]})

    by_lang: Dict[str, List[bool]] = {}
    for q, h in zip(questions, hits_at_5):
        by_lang.setdefault(q["lang"], []).append(h)
    return {
        "setup": name,
        "recall_at_5": round(sum(hits_at_5) / max(len(hits_at_5), 1), 3),
        "recall_by_lang": {k: round(sum(v) / len(v), 3) for k, v in by_lang.items()},
        "faithfulness": round(statistics.mean(faith), 3) if faith else None,
        "refusal": f"{sum(refusals)}/{len(refusals)}",
        "false_refusals": sum(1 for row in rows if not row.get("trick") and row["refused"]),
        "p95_latency_s": round(p95(latencies), 2),
        "rows": rows,
    }


def eligibility_accuracy(store: Store, path: str) -> Optional[Dict[str, Any]]:
    """Persona file (Person B): [{"id", "profile": {...}, "labels": {"<slug>": "eligible|maybe|no"}}]."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        personas = json.load(fh)
    total = correct = 0
    misses = []
    for p in personas:
        labels = p.get("labels") or {}
        schemes = [s for s in (store.get_scheme(slug) for slug in labels) if s]
        verdicts = {v.slug: v.verdict for v in evaluate(p["profile"], schemes)}
        for slug, gold in labels.items():
            if slug not in verdicts:
                continue
            total += 1
            if verdicts[slug] == gold:
                correct += 1
            else:
                misses.append({"persona": p.get("id"), "slug": slug, "gold": gold, "got": verdicts[slug]})
    return {"accuracy": round(correct / total, 3) if total else None, "pairs": total, "misses": misses}


def table(results: List[Dict[str, Any]], elig: Optional[Dict[str, Any]]) -> str:
    lines = ["| Setup | Recall@5 | Faithfulness | Eligibility accuracy | Refusal (trick) | False refusals | "
             "p95 latency (s) |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in results:
        acc = "—"
        if r["setup"].startswith("+ Rule engine"):
            acc = f"{elig['accuracy']:.0%}" if elig and elig.get("accuracy") is not None else "n/a (no profiles.json)"
        faith = f"{r['faithfulness']:.0%}" if r["faithfulness"] is not None else "—"
        lines.append(f"| {r['setup']} | {r['recall_at_5']:.0%} | {faith} | {acc} | {r['refusal']} | "
                     f"{r['false_refusals']} | {r['p95_latency_s']} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=os.path.join(HERE, "questions.json"))
    ap.add_argument("--profiles", default=os.path.join(HERE, "profiles.json"))
    ap.add_argument("--corpus", default="", help="JSONL of parsed schemes -> in-memory store (offline/CI)")
    ap.add_argument("--setups", default="1,2,3,4")
    ap.add_argument("--limit", type=int, default=0, help="only the first N questions (smoke runs)")
    ap.add_argument("--min-recall", type=float, default=0.0, help="fail (exit 1) if the last setup is below this")
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    args = ap.parse_args()

    with open(args.questions, encoding="utf-8") as fh:
        data = json.load(fh)
    questions = data["questions"][:args.limit] if args.limit else data["questions"]
    trick = data["trick"]

    store = load_corpus(args.corpus) if args.corpus else get_store()
    set_store(store)
    present = {s["slug"] for s in store.list_schemes()}
    missing = sorted({g for q in questions for g in q["gold_slugs"]} - present)
    if missing:
        print(f"warning: gold schemes not in corpus: {missing}", file=sys.stderr)

    usage.reset()
    results = []
    for n in [int(x) for x in args.setups.split(",")]:
        name, cfg = SETUPS[n]
        print(f"running setup {n}: {name} ...", file=sys.stderr)
        results.append(run_setup(name, cfg, questions, trick, store))
    elig = eligibility_accuracy(store, args.profiles)

    md = table(results, elig)
    judge = "lexical (offline)" if get_llm().is_fake else "Gemini judge"
    header = (f"Questions: {len(questions)} ({', '.join(f'{k}={v}' for k, v in sorted(_lang_counts(questions).items()))}),"
              f" trick: {len(trick)}, schemes in corpus: {len(present)}, faithfulness judge: {judge}")
    print(header)
    print(md)
    for r in results:
        print(f"  {r['setup']}: recall by language {r['recall_by_lang']}")

    os.makedirs(args.out, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with open(os.path.join(args.out, f"eval_{stamp}.json"), "w", encoding="utf-8") as fh:
        json.dump({"header": header, "results": results, "eligibility": elig, "usage": usage.snapshot()}, fh,
                  ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, "latest.md"), "w", encoding="utf-8") as fh:
        fh.write(f"{header}\n\n{md}\n")

    if args.min_recall and results[-1]["recall_at_5"] < args.min_recall:
        print(f"FAIL: recall@5 {results[-1]['recall_at_5']} < {args.min_recall}", file=sys.stderr)
        sys.exit(1)


def _lang_counts(questions: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for q in questions:
        out[q["lang"]] = out.get(q["lang"], 0) + 1
    return out


if __name__ == "__main__":
    main()
