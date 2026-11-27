"""Section-aware chunking: each scheme section is split on sentence boundaries into ~500-token
chunks with a small overlap. Chunks never cross a section, so a citation always names one section.

Token counts are estimated (no tokenizer dependency): ~4 chars/token for Latin text and
~2.5 chars/token for Devanagari, which is close enough for Gemini's tokenizer at this size.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

TARGET_TOKENS = 500
OVERLAP_TOKENS = 60

SECTION_ORDER = ["details", "benefits", "eligibility", "exclusions", "application_process",
                 "documents_required", "faq"]

# Sentence ends: . ? ! and the Devanagari danda । ; also numbered-list starts like "2. ".
_SENT_SPLIT = re.compile(r"(?<=[.?!।])\s+|(?<=\S)\s+(?=\d{1,2}\.\s)")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


@dataclass
class Chunk:
    section: str
    lang: str
    text: str
    page: Optional[int] = None
    meta: Dict[str, str] = field(default_factory=dict)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    deva = len(_DEVANAGARI.findall(text))
    other = len(text) - deva
    return int(other / 4 + deva / 2.5) + 1


def split_sentences(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


def _hard_split(sentence: str, max_tokens: int) -> List[str]:
    """Split one over-long sentence by words."""
    words, out, cur = sentence.split(" "), [], []
    for w in words:
        cur.append(w)
        if estimate_tokens(" ".join(cur)) >= max_tokens:
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def chunk_section(text: str, target: int = TARGET_TOKENS, overlap: int = OVERLAP_TOKENS) -> List[str]:
    sentences: List[str] = []
    for s in split_sentences(text):
        sentences.extend(_hard_split(s, target) if estimate_tokens(s) > target else [s])

    chunks: List[str] = []
    cur: List[str] = []
    cur_tokens = 0
    for s in sentences:
        t = estimate_tokens(s)
        if cur and cur_tokens + t > target:
            chunks.append(" ".join(cur))
            # carry the last sentences (up to `overlap` tokens) into the next chunk
            carry: List[str] = []
            carry_tokens = 0
            for prev in reversed(cur):
                pt = estimate_tokens(prev)
                if carry_tokens + pt > overlap:
                    break
                carry.insert(0, prev)
                carry_tokens += pt
            cur, cur_tokens = carry, carry_tokens
        cur.append(s)
        cur_tokens += t
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def chunk_scheme(name: str, sections: Dict[str, str], lang: str = "en",
                 target: int = TARGET_TOKENS, overlap: int = OVERLAP_TOKENS) -> List[Chunk]:
    """Chunk every section of one scheme in one language.

    Each chunk's text is prefixed with the scheme name and section so it is self-describing for
    both BM25 and the embedding (a chunk about "Rs. 6000 per year" still matches "PM-KISAN").
    """
    ordered = [s for s in SECTION_ORDER if s in sections] + [s for s in sections if s not in SECTION_ORDER]
    out: List[Chunk] = []
    for section in ordered:
        body = sections.get(section) or ""
        if not body.strip():
            continue
        label = section.replace("_", " ").title()
        for piece in chunk_section(body, target, overlap):
            out.append(Chunk(section=section, lang=lang, text=f"{name} | {label}: {piece}"))
    return out
