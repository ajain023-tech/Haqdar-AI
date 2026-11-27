"""myScheme scraper: sitemap -> Playwright -> raw cache (English + Hindi).

myScheme pages render with JavaScript, so each public scheme page is loaded in headless Chromium.
While the page renders, the scraper keeps the JSON the page itself fetches for its content
(scheme details, documents, FAQs) plus the rendered text. Nothing is called directly, no keys are used.

Politeness: one page at a time, about one request per second, raw results cached on disk so a
re-run only fetches what is missing or stale. Only allowlisted government domains are fetched.

    python -m ingest.scraper --slugs pm-kisan,pmuy          # fetch two schemes (en + hi)
    python -m ingest.scraper --discover --limit 250          # sitemap + seed list, first 250
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import httpx

BASE = "https://www.myscheme.gov.in"
SITEMAP = f"{BASE}/sitemap.xml"
RAW_DIR = os.path.join("data", "raw", "myscheme")
SEED_FILE = os.path.join(os.path.dirname(__file__), "seed_slugs.txt")
ALLOWED = ("myscheme.gov.in",)          # pages we fetch
ALLOWED_SUFFIXES = (".gov.in", ".nic.in")  # links we keep as sources
DELAY_S = 3.0          # the site throttles faster crawls ("Something went wrong")
BACKOFF_S = 60.0
MAX_CONSECUTIVE_ERRORS = 5
UA = "HaqdarAI-research-scraper/0.1 (+https://github.com/ajain023-tech/Haqdar-AI)"

# Content endpoints the scheme page calls while rendering; we keep their bodies.
CONTENT_PATTERNS = {
    "scheme": re.compile(r"/api/apisetu/schemes\?slug="),
    "documents": re.compile(r"/api/apisetu/schemes/[0-9a-f]+/documents"),
    "faqs": re.compile(r"/api/apisetu/schemes/[0-9a-f]+/faqs"),
}


def allowed_url(url: str, fetch: bool = True) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if fetch:
        return any(host == d or host.endswith("." + d) for d in ALLOWED)
    return host.endswith(ALLOWED_SUFFIXES) or any(host == d or host.endswith("." + d) for d in ALLOWED)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------ discovery
def sitemap_slugs(client: httpx.Client) -> List[str]:
    """Scheme slugs listed in the sitemap index (and its child sitemaps)."""
    slugs: List[str] = []
    try:
        idx = client.get(SITEMAP).text
    except httpx.HTTPError:
        return slugs
    maps = re.findall(r"<loc>([^<]+\.xml)</loc>", idx) or [SITEMAP]
    for m in dict.fromkeys(maps):
        if not allowed_url(m):
            continue
        try:
            body = client.get(m).text
        except httpx.HTTPError:
            continue
        slugs += re.findall(r"<loc>https://www\.myscheme\.gov\.in/schemes/([^<\"/?#]+)</loc>", body)
        time.sleep(DELAY_S)
    return list(dict.fromkeys(slugs))


def seed_slugs() -> List[str]:
    if not os.path.exists(SEED_FILE):
        return []
    with open(SEED_FILE, encoding="utf-8") as fh:
        return [s.strip() for s in fh if s.strip() and not s.startswith("#")]


def discover(client: httpx.Client) -> List[str]:
    """Sitemap first; the seed list (slugs from the public HF dump) fills what the sitemap lacks."""
    return list(dict.fromkeys(sitemap_slugs(client) + seed_slugs()))


# ------------------------------------------------------------------ fetching
@dataclass
class RawPage:
    slug: str
    lang: str
    source_url: str
    fetched_at: str
    status: str                     # ok | not_found | error
    content: Dict[str, Any]         # scheme / documents / faqs JSON bodies
    text: str                       # rendered page text (fallback parser input)
    links: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


def raw_path(slug: str, lang: str) -> str:
    return os.path.join(RAW_DIR, f"{slug}.{lang}.json")


def load_raw(slug: str, lang: str) -> Optional[Dict[str, Any]]:
    path = raw_path(slug, lang)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_raw(page: RawPage) -> None:
    os.makedirs(RAW_DIR, exist_ok=True)
    with open(raw_path(page.slug, page.lang), "w", encoding="utf-8") as fh:
        json.dump(page.to_dict(), fh, ensure_ascii=False)


class Scraper:
    """One headless browser, one page at a time."""

    def __init__(self, delay_s: float = DELAY_S, timeout_ms: int = 45000) -> None:
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch()
        self._ctx = self._browser.new_context(user_agent=UA, locale="en-IN")
        self.delay_s = delay_s
        self.timeout_ms = timeout_ms
        self._last = 0.0

    def close(self) -> None:
        self._ctx.close()
        self._browser.close()
        self._pw.stop()

    def __enter__(self) -> "Scraper":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _wait_turn(self) -> None:
        gap = time.time() - self._last
        if gap < self.delay_s:
            time.sleep(self.delay_s - gap)
        self._last = time.time()

    def fetch(self, slug: str, lang: str = "en") -> RawPage:
        url = f"{BASE}/schemes/{slug}" if lang == "en" else f"{BASE}/{lang}/schemes/{slug}"
        assert allowed_url(url)
        self._wait_turn()
        content: Dict[str, Any] = {}
        page = self._ctx.new_page()

        def on_response(resp) -> None:
            for key, pat in CONTENT_PATTERNS.items():
                if pat.search(resp.url) and resp.status == 200:
                    try:
                        content[key] = resp.json()
                    except Exception:  # noqa: BLE001 - non-JSON body; the rendered text still counts
                        pass

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
            text = page.inner_text("body")
            links = [h for h in page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
                     if allowed_url(h, fetch=False)]
            scheme = content.get("scheme") or {}
            found = bool((scheme.get("data") or {}).get(lang) or (scheme.get("data") or {}).get("en"))
            status = "ok" if found else ("not_found" if "not found" in text.lower() else "error")
        except Exception as exc:  # noqa: BLE001
            text, links, status = f"{type(exc).__name__}: {exc}", [], "error"
        finally:
            page.close()
        return RawPage(slug, lang, url, now_iso(), status, content, text, sorted(set(links)))


def scrape(slugs: Iterable[str], langs: Iterable[str] = ("en",), max_age_hours: float = 20,
           log=print) -> Dict[str, int]:
    """Fetch every slug x lang unless a fresh copy is cached. Returns counts by status."""
    counts: Dict[str, int] = {}
    todo = []
    for slug in slugs:
        for lang in langs:
            cached = load_raw(slug, lang)
            if cached and cached.get("status") == "ok":
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached["fetched_at"])).total_seconds()
                if age < max_age_hours * 3600:
                    counts["cached"] = counts.get("cached", 0) + 1
                    continue
            todo.append((slug, lang))
    if not todo:
        return counts
    errors_in_a_row = 0
    with Scraper() as s:
        for i, (slug, lang) in enumerate(todo, 1):
            page = s.fetch(slug, lang)
            if page.status == "error":
                time.sleep(BACKOFF_S)  # back off once, then retry this page
                page = s.fetch(slug, lang)
            if page.status == "error":
                errors_in_a_row += 1
                if not (load_raw(slug, lang) or {}).get("status") == "ok":
                    save_raw(page)  # never overwrite a good cached copy with an error page
            else:
                errors_in_a_row = 0
                save_raw(page)
            counts[page.status] = counts.get(page.status, 0) + 1
            if errors_in_a_row >= MAX_CONSECUTIVE_ERRORS:
                counts["stopped_early"] = len(todo) - i
                log(f"  {errors_in_a_row} errors in a row: the site is throttling us; stopping. "
                    f"Cached pages are kept, re-run later to continue.")
                break
            if i % 20 == 0:
                log(f"  fetched {i}/{len(todo)} {counts}")
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slugs", default="")
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--langs", default="en", help="en,hi also fetches the Hindi page (kept only if it has Hindi content)")
    args = ap.parse_args()
    if args.slugs:
        slugs = args.slugs.split(",")
    else:
        with httpx.Client(timeout=30, headers={"User-Agent": UA}) as c:
            slugs = discover(c)
    if args.limit:
        slugs = slugs[:args.limit]
    print(f"{len(slugs)} schemes to check")
    print(scrape(slugs, args.langs.split(",")))


if __name__ == "__main__":
    main()
