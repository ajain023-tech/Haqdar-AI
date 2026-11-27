"""Verification workflow: a person checks extracted criteria against the guideline PDF.

    python -m ingest.verify export --top 50              # -> verification/checklist.csv to fill in by hand
    python -m ingest.verify import verification/checklist.csv   # rows marked verified -> 'verified'
    python -m ingest.verify cross-check --top 50         # page vs guideline PDF; disagreements are flagged

Status meanings: verified = criteria checked by hand against the guideline PDF;
auto = LLM/regex extracted, not reviewed; stale = not re-checked for 30+ days, or changed after verification.
When the myScheme page and the PDF disagree, the scheme is flagged (criteria["_conflicts"]) instead of
picking one side.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from io import BytesIO
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.rag.store import Store, get_store  # noqa: E402
from ingest import scraper  # noqa: E402
from ingest.criteria import diff_criteria, extract_criteria, normalise  # noqa: E402

OUT_DIR = "verification"
COLUMNS = ["slug", "name", "official_link", "guideline_pdf", "eligibility_text", "extracted_criteria",
           "verified", "corrected_criteria", "notes"]


def top_slugs(store: Store, n: int) -> List[str]:
    """'Top' = the seed order (eval gold schemes first, then central, then the state)."""
    known = {s["slug"] for s in store.list_schemes()}
    order = [s for s in scraper.seed_slugs() if s in known]
    order += sorted(known - set(order))
    return order[:n]


def _eligibility_text(slug: str) -> str:
    raw = scraper.load_raw(slug, "en")
    if not raw:
        return ""
    from ingest.parse import parse_raw
    rec = parse_raw({"en": raw})
    return (rec or {}).get("sections", {}).get("en", {}).get("eligibility", "")[:1500]


def export(store: Store, top: int, path: str) -> int:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    rows = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for slug in top_slugs(store, top):
            s = store.get_scheme(slug)
            if not s:
                continue
            crit = {k: v for k, v in (s.get("criteria") or {}).items() if not k.startswith("_")}
            w.writerow({"slug": slug, "name": s["name"], "official_link": s.get("source_url"),
                        "guideline_pdf": s.get("pdf_url") or "", "eligibility_text": _eligibility_text(slug),
                        "extracted_criteria": json.dumps(crit, ensure_ascii=False),
                        "verified": "", "corrected_criteria": "", "notes": ""})
            rows += 1
    return rows


def import_checklist(store: Store, path: str) -> Dict[str, int]:
    """Rows with verified in (y, yes, 1, true) become 'verified'; corrected_criteria (JSON) replaces criteria."""
    counts = {"verified": 0, "corrected": 0, "skipped": 0}
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("verified") or "").strip().lower() not in ("y", "yes", "1", "true"):
                counts["skipped"] += 1
                continue
            s = store.get_scheme(row["slug"])
            if not s:
                counts["skipped"] += 1
                continue
            fixed = (row.get("corrected_criteria") or "").strip()
            if fixed:
                crit = normalise(json.loads(fixed), s.get("state") or "ALL")
                crit["_extracted_by"] = "human"
                store.set_criteria(row["slug"], crit)
                counts["corrected"] += 1
            store.set_verification(row["slug"], "verified")
            counts["verified"] += 1
    return counts


def _pdf_text(url: str, client: httpx.Client) -> Optional[str]:
    host = (urlparse(url).hostname or "").lower()
    if not host.endswith((".gov.in", ".nic.in")) and host != "gov.in":
        return None  # allowlist: official sources only
    from pypdf import PdfReader
    r = client.get(url, follow_redirects=True)
    if r.status_code != 200 or b"%PDF" not in r.content[:1024]:
        return None
    reader = PdfReader(BytesIO(r.content))
    return "\n".join((p.extract_text() or "") for p in reader.pages[:15])


def cross_check(store: Store, top: int) -> List[Dict[str, Any]]:
    """Extract criteria from the guideline PDF and compare with the page's. Needs an LLM to be useful."""
    conflicts = []
    with httpx.Client(timeout=60, headers={"User-Agent": scraper.UA}) as client:
        for slug in top_slugs(store, top):
            s = store.get_scheme(slug)
            if not s or not s.get("pdf_url"):
                continue
            try:
                text = _pdf_text(s["pdf_url"], client)
            except Exception:  # noqa: BLE001
                text = None
            if not text:
                continue
            pdf_crit = extract_criteria({"eligibility": text[:6000]}, s.get("state") or "ALL")
            fields = diff_criteria(s.get("criteria") or {}, pdf_crit)
            # Only fields the PDF actually constrains count as a disagreement.
            fields = [f for f in fields if pdf_crit.get(f) not in (None, "any", ["ALL"])]
            if fields:
                crit = dict(s.get("criteria") or {}, _conflicts=fields)
                store.set_criteria(slug, crit)
                conflicts.append({"slug": slug, "fields": fields, "page": {f: s["criteria"].get(f) for f in fields},
                                  "pdf": {f: pdf_crit.get(f) for f in fields}})
    if conflicts:
        store.add_change_log(0, 0, 0, {"source": "cross-check", "conflicts": conflicts,
                                       "summary": f"{len(conflicts)} page/PDF disagreements flagged"})
    return conflicts


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--top", type=int, default=50)
    e.add_argument("--out", default=os.path.join(OUT_DIR, "checklist.csv"))
    i = sub.add_parser("import")
    i.add_argument("path")
    c = sub.add_parser("cross-check")
    c.add_argument("--top", type=int, default=50)
    args = ap.parse_args()
    store = get_store()
    if args.cmd == "export":
        print(f"wrote {export(store, args.top, args.out)} rows to {args.out}")
    elif args.cmd == "import":
        print(import_checklist(store, args.path))
    else:
        for c in cross_check(store, args.top):
            print(json.dumps(c, ensure_ascii=False))


if __name__ == "__main__":
    main()
