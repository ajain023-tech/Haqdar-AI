"""Turn a Locust run + the server's /metrics into the Day 6 numbers: p95 latency and cost per 1,000 queries.

    python -m loadtest.report --csv loadtest/results_stats.csv --api https://<api>
"""
from __future__ import annotations

import argparse
import csv

import httpx


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="loadtest/results_stats.csv")
    ap.add_argument("--api", required=True)
    args = ap.parse_args()

    print("| Endpoint | Requests | Failures | p50 (ms) | p95 (ms) | p99 (ms) | RPS |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    with open(args.csv, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            print(f"| {row['Name']} | {row['Request Count']} | {row['Failure Count']} | {row['50%']} | "
                  f"{row['95%']} | {row['99%']} | {float(row['Requests/s']):.1f} |")

    m = httpx.get(f"{args.api.rstrip('/')}/metrics", timeout=30).json()
    print()
    print(f"queries served: {int(m.get('queries', 0))}")
    print(f"LLM calls: {int(m.get('llm_calls', 0))}, tokens in/out: {int(m.get('llm_in', 0))}/{int(m.get('llm_out', 0))}")
    print(f"embedding calls: {int(m.get('embed_calls', 0))}")
    print(f"cost per 1,000 queries at paid-tier prices: ${m.get('est_cost_usd_per_1000_queries_if_paid')} "
          "(₹0 on the free tier while under its limits)")


if __name__ == "__main__":
    main()
