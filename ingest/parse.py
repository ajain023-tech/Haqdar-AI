"""Parse a scraped myScheme page into a `schemes` row + per-language sections.

Primary input is the JSON the page fetched while rendering (structured fields, markdown bodies).
If that is missing, the rendered page text goes through the same section parser as the HF loader.
"""
from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional

from scripts.load_hf_dataset import (MYSCHEME_URL, bootstrap_criteria, parse_pdf_text, pick_category,
                                     split_documents)

CATEGORY_MAP = [  # myScheme category label -> our category
    ("agricultur", "agriculture"), ("health", "health"), ("education", "education"), ("housing", "housing"),
    ("women", "women_child"), ("child", "women_child"), ("skill", "employment"), ("employment", "employment"),
    ("business", "business"), ("entrepreneur", "business"), ("banking", "business"), ("insurance", "insurance"),
    ("pension", "pension"), ("disab", "disability"), ("social welfare", "social_welfare"),
]


def md_to_text(md: Optional[str]) -> str:
    """Markdown body -> plain text that keeps list items and headings as sentences."""
    if not md:
        return ""
    t = html.unescape(html.unescape(md))  # bodies arrive double-escaped ("&amp;#39;")
    t = re.sub(r"<[^>]+>", " ", t)                               # stray HTML
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", t)  # links keep their URL
    t = re.sub(r"^#{1,6}\s*(.+)$", r"\1:", t, flags=re.M)         # headings
    t = re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "- ", t, flags=re.M)     # list markers
    t = re.sub(r"[*_`]{1,3}", "", t)                              # emphasis
    lines = [ln.strip() for ln in t.splitlines()]
    out: List[str] = []
    for ln in lines:
        if not ln:
            continue
        if ln.startswith("- ") and out and not out[-1].endswith((".", ":", "?", "!", "।")):
            out[-1] += "."
        out.append(ln[2:] if ln.startswith("- ") else ln)
    text = " ".join(s if s.endswith((".", ":", "?", "!", "।")) else s + "." for s in out)
    return re.sub(r"\s+", " ", text).strip()


def _label(v: Any) -> Optional[str]:
    if isinstance(v, dict):
        return v.get("label")
    return v if isinstance(v, str) else None


def map_category(labels: List[str], name: str, tags: List[str], details: str) -> str:
    """myScheme's own category labels first, then tags, then the keyword vote."""
    for pool in (" ".join(labels).lower(), " ".join(tags).lower()):
        for key, cat in CATEGORY_MAP:
            if key in pool and cat != "social_welfare":
                return cat
    return pick_category(name, " ".join(tags), details)


def _slate_items(nodes: Any) -> List[str]:
    """Text of every list item in a Slate-style rich-text tree (myScheme's documents_required)."""
    out: List[str] = []

    def text_of(n: Any) -> str:
        if isinstance(n, dict):
            return n.get("text", "") + "".join(text_of(c) for c in n.get("children") or [])
        if isinstance(n, list):
            return "".join(text_of(c) for c in n)
        return ""

    def walk(n: Any) -> None:
        if isinstance(n, list):
            for c in n:
                walk(c)
        elif isinstance(n, dict):
            if n.get("type") == "list_item":
                t = re.sub(r"\s+", " ", html.unescape(text_of(n))).strip(" .")
                if t:
                    out.append(t)
            else:
                walk(n.get("children") or [])
    walk(nodes)
    return out


def document_list(doc_json: Dict[str, Any], lang: str = "en") -> List[str]:
    d = ((doc_json or {}).get("data") or {}).get(lang) or {}
    items = []
    for it in _slate_items(d.get("documents_required") or []):
        label = re.split(r"\s[-–]\s|:", it, 1)[0].strip()
        if 2 < len(label) <= 90 and label.lower() not in {i.lower() for i in items}:
            items.append(label)
    return items[:25]


def _documents(doc_json: Dict[str, Any], lang: str) -> str:
    d = ((doc_json or {}).get("data") or {}).get(lang) or {}
    return md_to_text(d.get("documentsRequired_md")) or ". ".join(document_list(doc_json, lang))


def _faqs(faq_json: Dict[str, Any], lang: str) -> str:
    d = ((faq_json or {}).get("data") or {}).get(lang) or {}
    parts = []
    for f in d.get("faqs") or []:
        q, a = (f.get("question") or "").strip(), md_to_text(f.get("answer_md"))
        if q and a:
            parts.append(f"{q.rstrip('?')}? {a}")
    return " ".join(parts)


def _sections(data: Dict[str, Any], content: Dict[str, Any], lang: str) -> Dict[str, str]:
    sc = data.get("schemeContent") or {}
    el = data.get("eligibilityCriteria") or {}
    steps = []
    for ch in data.get("applicationProcess") or []:
        body = md_to_text(ch.get("process_md"))
        if body:
            steps.append(f"{ch.get('mode') or 'Process'}: {body}")
    sections = {
        "details": md_to_text(sc.get("detailedDescription_md")) or (sc.get("briefDescription") or ""),
        "benefits": md_to_text(sc.get("benefits_md")),
        "eligibility": md_to_text(el.get("eligibilityDescription_md")),
        "exclusions": md_to_text(sc.get("exclusions_md")),
        "application_process": " ".join(steps),
        "documents_required": _documents(content.get("documents"), lang),
        "faq": _faqs(content.get("faqs"), lang),
    }
    return {k: v for k, v in sections.items() if v}


def parse_raw(raw_by_lang: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """{"en": raw, "hi": raw?} -> {"scheme": row, "sections": {"en": {...}, "hi": {...}}, "references": [...]}."""
    en_raw = raw_by_lang.get("en")
    if not en_raw or en_raw.get("status") != "ok":
        return None
    slug = en_raw["slug"]
    sdata = ((en_raw.get("content") or {}).get("scheme") or {}).get("data") or {}
    en = sdata.get("en")
    if not en:  # JSON missing: parse the rendered text instead
        return parse_pdf_text(slug, en_raw.get("text") or "")

    b = en.get("basicDetails") or {}
    sections_by_lang = {"en": _sections(en, en_raw.get("content") or {}, "en")}
    name_hi = benefits_hi = None
    hi_raw = raw_by_lang.get("hi")
    if hi_raw and hi_raw.get("status") == "ok":
        hdata = ((hi_raw.get("content") or {}).get("scheme") or {}).get("data") or {}
        hi = hdata.get("hi")
        if hi:  # only real Hindi content; a Hindi UI over English text is not a translation
            sections_by_lang["hi"] = _sections(hi, hi_raw.get("content") or {}, "hi")
            name_hi = (hi.get("basicDetails") or {}).get("schemeName")
            benefits_hi = sections_by_lang["hi"].get("benefits")

    level = (_label(b.get("level")) or "").lower()
    state = _label(b.get("state")) if level == "state" else None
    en_secs = sections_by_lang["en"]
    tags = [t for t in b.get("tags") or [] if isinstance(t, str)]
    cats = [c for c in (_label(x) for x in b.get("schemeCategory") or []) if c]
    refs = [{"title": r.get("title"), "url": (r.get("url") or "").strip()}
            for r in (en.get("schemeContent") or {}).get("references") or [] if r.get("url")]
    pdf = next((r["url"] for r in refs if r["url"].lower().endswith(".pdf")), None)

    scheme = {
        "slug": slug,
        "name": b.get("schemeName") or slug,
        "name_hi": name_hi,
        "ministry": _label(b.get("nodalMinistryName")) or _label(b.get("nodalDepartmentName")),
        "state": state or "ALL",
        "category": map_category(cats, b.get("schemeName") or "", tags, en_secs.get("details", "")),
        "benefits": en_secs.get("benefits"),
        "benefits_hi": benefits_hi,
        "documents_required": document_list((en_raw.get("content") or {}).get("documents"))
                              or split_documents(en_secs.get("documents_required", "")),
        "criteria": bootstrap_criteria(en_secs, state or "ALL"),
        "verification": "auto",
        "status": "active",
        "source_url": MYSCHEME_URL.format(slug=slug),
        "pdf_url": pdf,
    }
    return {"scheme": scheme, "sections": sections_by_lang, "references": refs, "tags": tags,
            "short_title": b.get("schemeShortTitle"), "fetched_at": en_raw.get("fetched_at")}
