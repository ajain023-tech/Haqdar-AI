"""Daily refresh: re-check every scheme, re-process only what changed, never delete.

    1. Discover scheme URLs (sitemap + seed list) and diff against the schemes we know.
    2. Fetch each page (cached raw copies younger than ~20 h are reused).
    3. content_hash of the cleaned text: unchanged -> only `last_checked` moves;
       changed or new -> re-parse, re-extract criteria, re-embed that scheme only (old copy -> scheme_versions).
    4. Known schemes missing from discovery or returning "not found" -> status inactive.
    5. Verification: a 'verified' scheme whose content changed becomes 'stale' until re-checked;
       any scheme not re-checked for 30+ days becomes 'stale'.
    6. One change_log row ("3 updated, 1 new") for the "Recently updated" panel.

    python -m ingest.refresh                     # full run (GitHub Actions cron)
    python -m ingest.refresh --slugs pm-kisan    # one scheme
    python -m ingest.refresh --offline           # re-process cached raw pages only, no network
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.rag.indexer import content_hash, index_scheme  # noqa: E402
from backend.rag.store import Store, get_store  # noqa: E402
from ingest import scraper  # noqa: E402
from ingest.criteria import extract_criteria  # noqa: E402
from ingest.parse import parse_raw  # noqa: E402

STATE = os.environ.get("HAQDAR_STATE", "Uttar Pradesh")   # central schemes + this state
STALE_AFTER_DAYS = 30


def _parse_dt(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None


def process_slug(slug: str, store: Store, langs: Iterable[str] = ("en",)) -> Dict[str, Any]:
    """Parse cached raw pages for one slug and index them. Returns {"slug", "status", ...}."""
    raws = {lang: scraper.load_raw(slug, lang) for lang in langs}
    first = raws.get("en")
    if not first:
        return {"slug": slug, "status": "missing_raw"}
    if first.get("status") == "not_found":
        return {"slug": slug, "status": "not_found"}
    if first.get("status") != "ok":
        return {"slug": slug, "status": "fetch_failed"}  # transient: keep the scheme as it is
    rec = parse_raw({k: v for k, v in raws.items() if v})
    if not rec:
        return {"slug": slug, "status": "parse_failed"}
    scheme = rec["scheme"]
    if scheme["state"] not in ("ALL", STATE) and STATE.lower() != "any":
        return {"slug": slug, "status": "out_of_scope"}

    new_hash = content_hash(rec["sections"])
    existing = store.get_scheme(slug)
    if existing and existing.get("content_hash") == new_hash:
        index_scheme(dict(scheme, criteria=existing.get("criteria") or {}, content_hash=new_hash,
                          verification=existing.get("verification")), rec["sections"], store=store)
        return {"slug": slug, "status": "unchanged"}

    # New or changed: re-extract criteria (the only LLM cost in ingestion) and re-embed this scheme.
    scheme["criteria"] = extract_criteria(rec["sections"].get("en", {}), scheme["state"])
    scheme["content_hash"] = new_hash
    was_verified = bool(existing and existing.get("verification") == "verified")
    scheme["verification"] = "stale" if was_verified else "auto"
    status = index_scheme(scheme, rec["sections"], store=store)
    return {"slug": slug, "status": status, "name": scheme["name"], "reverify": was_verified}


def mark_stale(store: Store, now: Optional[datetime] = None) -> List[str]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=STALE_AFTER_DAYS)
    out = []
    for s in store.list_schemes():
        checked = _parse_dt(s.get("last_checked"))
        if checked and checked < cutoff and s.get("verification") != "stale":
            store.set_verification(s["slug"], "stale")
            out.append(s["slug"])
    return out


def run(slugs: Optional[List[str]] = None, store: Optional[Store] = None, fetch: bool = True,
        scrape_fn: Callable[..., Dict[str, int]] = scraper.scrape, log: Callable[[str], None] = print,
        limit: int = 0) -> Dict[str, Any]:
    store = store or get_store()
    full_run = slugs is None
    if full_run:
        import httpx
        with httpx.Client(timeout=30, headers={"User-Agent": scraper.UA}) as c:
            slugs = scraper.discover(c) if fetch else [f.rsplit(".", 2)[0] for f in _cached_files()]
    slugs = list(dict.fromkeys(slugs))
    if limit:
        slugs = slugs[:limit]
    log(f"refresh: {len(slugs)} schemes")
    if fetch:
        log(f"fetch: {scrape_fn(slugs, log=log)}")

    results = [process_slug(s, store) for s in slugs]
    by_status: Dict[str, List[str]] = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r["slug"])

    gone = set(by_status.get("not_found", []))
    if full_run and fetch:  # absence only means "removed" when we actually re-discovered the site
        seen = set(slugs)
        gone |= {s["slug"] for s in store.list_schemes() if s["slug"] not in seen}
    inactive = store.mark_inactive(sorted(gone))
    stale = mark_stale(store)

    added, updated = by_status.get("added", []), by_status.get("updated", [])
    details = {
        "source": "myscheme.gov.in",
        "new": added[:50], "updated": updated[:50], "inactive": sorted(gone)[:50], "stale": stale[:50],
        "reverify": [r["slug"] for r in results if r.get("reverify")],
        "unchanged": len(by_status.get("unchanged", [])),
        "failed": {k: v[:20] for k, v in by_status.items() if k in ("parse_failed", "missing_raw", "fetch_failed")},
        "summary": f"{len(updated)} updated, {len(added)} new, {inactive} inactive",
    }
    store.add_change_log(len(added), len(updated), inactive, details)
    log(f"refresh done: {details['summary']} ({details['unchanged']} unchanged)")
    return {"added": len(added), "updated": len(updated), "inactive": inactive, "stale": len(stale),
            "unchanged": details["unchanged"]}


def _cached_files() -> List[str]:
    if not os.path.isdir(scraper.RAW_DIR):
        return []
    return sorted(f for f in os.listdir(scraper.RAW_DIR) if f.endswith(".en.json"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slugs", default="")
    ap.add_argument("--offline", action="store_true", help="re-process cached raw pages, no fetching")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    run(args.slugs.split(",") if args.slugs else None, fetch=not args.offline, limit=args.limit)


if __name__ == "__main__":
    main()
