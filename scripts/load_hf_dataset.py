"""Day 1 bootstrap: load the Hugging Face myScheme dump (shrijayan/gov_myscheme) into `schemes` + `chunks`.

The dataset is ~2,150 PDFs, one per myScheme page, named by the myScheme slug. Each PDF is parsed into
the same sections the scraper produces (details, benefits, eligibility, exclusions, application_process,
documents_required, faq), then indexed (chunk + embed). Community scrape: Person B's own scraper replaces
it from Day 2; rows keep verification='auto' until hand-checked.

Usage:
    python -m scripts.load_hf_dataset --state "Uttar Pradesh" --limit 250
    python -m scripts.load_hf_dataset --slugs pm-kisan,pmay-g --dump data/hf_schemes.jsonl
    python -m scripts.load_hf_dataset --from-jsonl data/hf_schemes.jsonl   # re-index a parsed dump offline
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.rag.indexer import content_hash, index_scheme  # noqa: E402
from backend.rag.store import get_store  # noqa: E402

DATASET = "shrijayan/gov_myscheme"
HF_API = f"https://huggingface.co/api/datasets/{DATASET}"
HF_FILE = f"https://huggingface.co/datasets/{DATASET}/resolve/main/text_data/{{slug}}.pdf"
MYSCHEME_URL = "https://www.myscheme.gov.in/schemes/{slug}"
CACHE_DIR = os.path.join("data", "raw", "hf")

HEADINGS: List[Tuple[str, str]] = [
    ("details", "Details"),
    ("benefits", "Benefits"),
    ("eligibility", "Eligibility"),
    ("exclusions", "Exclusions"),
    ("application_process", "Application Process"),
    ("documents_required", "Documents Required"),
    ("faq", "Frequently Asked Questions"),
]
END_MARKERS = ["Sources And References", "Was this helpful?", "News and Updates"]

CATEGORY_KEYWORDS = [
    ("agriculture", ["farmer", "kisan", "agricultur", "crop", "krishi", "fisher", "irrigation", "dairy"]),
    ("health", ["health", "hospital", "medical", "treatment", "ayushman", "insurance cover"]),
    ("education", ["scholarship", "student", "education", "school", "college", "fellowship"]),
    ("housing", ["housing", "house", "awas", "shelter"]),
    ("pension", ["pension", "old age", "widow"]),
    ("insurance", ["insurance", "bima"]),
    ("business", ["loan", "entrepreneur", "enterprise", "msme", "startup", "credit", "vendor"]),
    ("women_child", ["girl", "women", "woman", "mother", "pregnan", "maternity", "child", "kanya", "beti"]),
    ("employment", ["employment", "skill", "training", "job", "wage", "labour", "labor", "worker"]),
    ("disability", ["disab", "divyang", "differently abled"]),
]

# Indian states / UTs as they appear on myScheme pages (used to tell state schemes from central ones).
STATES = ["Andaman And Nicobar Islands", "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chandigarh",
          "Chhattisgarh", "Dadra And Nagar Haveli And Daman And Diu", "Delhi", "Goa", "Gujarat", "Haryana",
          "Himachal Pradesh", "Jammu And Kashmir", "Jharkhand", "Karnataka", "Kerala", "Ladakh", "Lakshadweep",
          "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Puducherry",
          "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand",
          "West Bengal"]

_MOJIBAKE = re.compile("[Â-ô][\u0080-¿ŒœŠšŸŽžƒˆ"
                       "˜–—‘-„†-•…‰‹›€™]+")


def fix_mojibake(text: str) -> str:
    """Undo UTF-8 text that was decoded as cp1252 (e.g. 'â€™' -> '’', 'à¤¹' -> 'ह')."""
    def _fix(m: "re.Match[str]") -> str:
        s = m.group(0)
        raw = bytearray()
        for ch in s:
            try:
                raw += ch.encode("cp1252")
            except UnicodeEncodeError:
                if ord(ch) > 255:
                    return s
                raw.append(ord(ch))  # bytes cp1252 leaves undefined (0x81, 0x8d, 0x9d...) came through as-is
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return s
    return _MOJIBAKE.sub(_fix, text)


def clean(text: str) -> str:
    text = fix_mojibake(text.replace("\t", " "))
    text = text.replace("\ufeff", "").replace("\u200b", "").replace("\u00a0", " ")
    return re.sub(r"[ \r\f\v]+", " ", text)


def _find_heading(text: str, heading: str, start: int, end: int) -> int:
    # Headings are glued to the text around them ("...burden.Benefits1. The...", "Girl ChildDetailsThe...").
    # Take the earliest glued occurrence; fall back to a capitalised standalone one.
    h = re.escape(heading)
    glued = [m.start() for pat in (rf"(?<=[^\sA-Za-z]){h}(?=[A-Z0-9\s])", rf"(?<=\S){h}(?=[A-Z0-9])")
             for m in [re.compile(pat).search(text, start, end)] if m]
    if glued:
        return min(glued)
    m = re.compile(rf"\b{h}(?=\s?[A-Z0-9])").search(text, start, end)
    return m.start() if m else -1


def parse_pdf_text(slug: str, raw: str) -> Optional[Dict[str, Any]]:
    text = clean(raw)
    flat = re.sub(r"\s+", " ", text)
    name_m = re.match(r"\s*(.+?)\s*Are you sure you want to sign out", flat)
    if not name_m:
        return None
    name = name_m.group(1).strip()

    anchor = flat.find("Check Eligibility")
    if anchor < 0:
        return None
    after = flat[anchor + len("Check Eligibility"):]
    name_pos = after.find(name)
    owner = after[:name_pos].strip() if name_pos > 0 else ""
    body_start = anchor + len("Check Eligibility") + (name_pos + len(name) if name_pos >= 0 else 0)
    tags_end = flat.find("Details", body_start)
    tags = flat[body_start:tags_end] if tags_end > 0 else ""
    tags = re.sub(r"Sign in to apply", "", tags).strip()

    end = len(flat)
    for marker in END_MARKERS:
        i = flat.find(marker, body_start)
        if 0 < i < end:
            end = i

    sections: Dict[str, str] = {}
    positions: List[Tuple[int, str, str]] = []
    cursor = tags_end if tags_end > 0 else body_start
    for key, heading in HEADINGS:
        pos = _find_heading(flat, heading, cursor, end)
        if pos >= 0:
            positions.append((pos, key, heading))
            cursor = pos + len(heading)
    for i, (pos, key, heading) in enumerate(positions):
        stop = positions[i + 1][0] if i + 1 < len(positions) else end
        body = flat[pos + len(heading):stop].strip(" .")
        if body:
            sections[key] = body
    if "details" not in sections and "eligibility" not in sections:
        return None

    state = "ALL"
    ministry = None
    if owner in STATES:
        state = owner
    elif owner:
        ministry = owner

    category = pick_category(name, tags, sections.get("details", ""))

    docs = split_documents(sections.get("documents_required", ""))
    return {
        "scheme": {
            "slug": slug, "name": name, "name_hi": None, "ministry": ministry, "state": state,
            "category": category, "benefits": sections.get("benefits"), "benefits_hi": None,
            "documents_required": docs, "criteria": bootstrap_criteria(sections, state),
            "verification": "auto", "status": "active",
            "source_url": MYSCHEME_URL.format(slug=slug), "pdf_url": None,
            "tags": tags,
        },
        "sections": {"en": sections},
    }


def pick_category(name: str, tags: str, details: str) -> str:
    """Keyword vote: name/tag hits count 3x, the first 600 chars of details 1x."""
    head, body = f"{name} {tags}".lower(), details[:600].lower()
    best, best_score = "social_welfare", 0
    for cat, kws in CATEGORY_KEYWORDS:
        score = sum(3 * head.count(k) + body.count(k) for k in kws)
        if score > best_score:
            best, best_score = cat, score
    return best


def split_documents(text: str) -> List[str]:
    """'1. Aadhaar Card: proof of identity.2. Age Proof: ...' -> ['Aadhaar Card', 'Age Proof', ...]"""
    if not text:
        return []
    # Numbered items ("1. Aadhaar Card: ..."), sentences glued together ("...child.Joint photo"),
    # or labels glued without punctuation ("BPL CardAadhaar card").
    splitter = r"(?:^|(?<=[\s.]))\d{1,2}\.\s*|(?<=[A-Za-z)])\.(?=[A-Z])|(?<=[a-z])(?=[A-Z][A-Za-z])"
    items = [p.strip() for p in re.split(splitter, text) if p and p.strip()]
    out = []
    for it in items:
        label = re.split(r"[:\-–(]", it, 1)[0].strip(" .")
        if 2 < len(label) <= 80 and label not in out and not label.lower().startswith(("at phase", "for ")):
            out.append(label)
    return out[:20]


def bootstrap_criteria(sections: Dict[str, str], state: str) -> Dict[str, Any]:
    """Cheap regex pass so the rule engine has *something* on Day 1. Person B's LLM extractor replaces it."""
    elig = sections.get("eligibility", "")
    crit: Dict[str, Any] = {"states": [state] if state != "ALL" else ["ALL"], "age": None, "gender": "any",
                            "occupation": None, "land_acres": None, "annual_income": None,
                            "social_category": None, "disability": None, "bpl": None}
    m = re.search(r"(?:between|from)\s+(?:the\s+ages?\s+of\s+)?(\d{1,2})\s*(?:-|to|and)\s*(\d{2,3})\s*years", elig, re.I)
    if m:
        crit["age"] = {"min": int(m.group(1)), "max": int(m.group(2))}
    inc = re.search(r"income[^.]{0,60}?(?:not exceed(?:ing)?|less than|below|up to|upto)\s*(?:Rs\.?|₹|INR)?\s*([\d,]+(?:\.\d+)?)\s*(lakh)?",
                    elig, re.I)
    if inc:
        val = float(inc.group(1).replace(",", ""))
        crit["annual_income"] = {"max": int(val * 100000) if inc.group(2) else int(val)}
    low = elig.lower()
    if re.search(r"\b(women|woman|female|girl)\b", low) and not re.search(r"\bmale\b", low.replace("female", "")):
        crit["gender"] = "female"
    if "below poverty line" in low or re.search(r"\bbpl\b", low):
        crit["bpl"] = True
    if "farmer" in low:
        crit["occupation"] = ["farmer"]
    cats = [c for c in ("SC", "ST", "OBC") if re.search(rf"\b{c}\b", elig)]
    if cats:
        crit["social_category"] = cats
    if re.search(r"disab|divyang", low):
        crit["disability"] = True
    return crit


def list_slugs(client: httpx.Client) -> List[str]:
    r = client.get(HF_API)
    r.raise_for_status()
    files = [s["rfilename"] for s in r.json().get("siblings", [])]
    return sorted({f[len("text_data/"):-4] for f in files
                   if f.startswith("text_data/") and f.endswith(".pdf") and " copy" not in f})


def fetch_pdf_text(client: httpx.Client, slug: str) -> Optional[str]:
    from io import BytesIO
    from pypdf import PdfReader

    import time

    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{slug}.pdf")
    if not os.path.exists(path):
        for attempt in range(5):
            try:
                r = client.get(HF_FILE.format(slug=slug), follow_redirects=True)
            except httpx.HTTPError:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                with open(path, "wb") as fh:
                    fh.write(r.content)
                break
            if r.status_code in (429, 500, 502, 503, 504):  # HF rate limit / hiccup: back off
                time.sleep(2 ** attempt)
                continue
            return None
        else:
            return None
    try:
        with open(path, "rb") as fh:
            reader = PdfReader(BytesIO(fh.read()))
        return "\n".join(p.extract_text() or "" for p in reader.pages)
    except Exception:  # noqa: BLE001 - a broken PDF should not stop the batch
        return None


def load(state: str = "Uttar Pradesh", limit: int = 0, slugs: Optional[List[str]] = None, dump: str = "",
         from_jsonl: str = "", workers: int = 8, store=None, log=print) -> Dict[str, int]:
    """Download (or read) + parse + index. Returns counts of added / updated / unchanged schemes."""
    store = store or get_store()
    records: List[Dict[str, Any]] = []
    if from_jsonl:
        with open(from_jsonl, encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh if line.strip()]
    else:
        client = httpx.Client(timeout=60.0, headers={"User-Agent": "haqdar-ai-loader"})
        slugs = slugs or list_slugs(client)
        log(f"{len(slugs)} scheme files in {DATASET}")

        def work(slug: str) -> Optional[Dict[str, Any]]:
            raw = fetch_pdf_text(client, slug)
            return parse_pdf_text(slug, raw) if raw else None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            records = [rec for rec in pool.map(work, slugs) if rec]
        log(f"parsed {len(records)} schemes")

    keep_state = None if state.lower() == "any" else state
    if keep_state:
        records = [r for r in records if r["scheme"]["state"] in ("ALL", keep_state)]
    # Central schemes first, then the chosen state, so a small limit still covers the big schemes.
    records.sort(key=lambda r: (r["scheme"]["state"] != "ALL", r["scheme"]["slug"]))
    if limit:
        records = records[:limit]

    if dump:
        os.makedirs(os.path.dirname(dump) or ".", exist_ok=True)
        with open(dump, "w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    counts = {"added": 0, "updated": 0, "unchanged": 0}
    for i, rec in enumerate(records, 1):
        scheme = {k: v for k, v in rec["scheme"].items() if k != "tags"}
        scheme["content_hash"] = content_hash(rec["sections"])
        counts[index_scheme(scheme, rec["sections"], store=store)] += 1
        if i % 25 == 0:
            log(f"  indexed {i}/{len(records)}")
    store.add_change_log(counts["added"], counts["updated"], 0,
                         {"source": DATASET, "unchanged": counts["unchanged"]})
    log(f"done: {counts}")
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="Uttar Pradesh", help="state to keep besides central schemes; 'any' = all")
    ap.add_argument("--limit", type=int, default=0, help="max schemes to index (0 = no limit)")
    ap.add_argument("--slugs", default="", help="comma-separated slugs to load (overrides the listing)")
    ap.add_argument("--dump", default="", help="also write parsed records to this JSONL file")
    ap.add_argument("--from-jsonl", default="", help="index records from a JSONL dump instead of downloading")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    load(args.state, args.limit, args.slugs.split(",") if args.slugs else None, args.dump, args.from_jsonl,
         args.workers)


if __name__ == "__main__":
    main()
