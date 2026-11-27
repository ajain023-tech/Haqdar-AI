"""Answer generation: cited [1][2], in the user's language, grounded only in retrieved chunks.

The LLM never decides eligibility: rule-engine verdicts are passed in as facts to explain.
Below the reranker-score threshold the answer is a fixed refusal, not a guess.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from backend.core.lang import LANG_NAMES, STOPWORDS
from backend.providers.llm import LLM, RateLimited, get_llm
from backend.rag.store import Hit, tokenize

REFUSAL = {
    "en": "I couldn't find this in official sources. Please check myscheme.gov.in or visit your nearest "
          "Common Service Centre (CSC).",
    "hi": "मुझे यह जानकारी आधिकारिक स्रोतों में नहीं मिली। कृपया myscheme.gov.in देखें या नज़दीकी जन सेवा केंद्र "
          "(CSC) पर जाएँ।",
    "hinglish": "Mujhe yeh jaankari official sources mein nahi mili. Kripya myscheme.gov.in dekhein ya nazdeeki "
                "Common Service Centre (CSC) jaayein.",
}
DISCLAIMER = {
    "en": "Unofficial assistant. Final eligibility is decided by the department; apply via the official link.",
    "hi": "यह अनौपचारिक सहायक है। अंतिम पात्रता विभाग तय करता है; आवेदन आधिकारिक लिंक से ही करें।",
    "hinglish": "Yeh unofficial assistant hai. Final eligibility department tay karta hai; apply official link se hi karein.",
}

SYSTEM = (
    "You are Haqdar AI, a helpful assistant for Indian citizens about government welfare schemes.\n"
    "Rules:\n"
    "1. Use ONLY the numbered sources. Every sentence with a fact ends with its source number like [1] or [1][3].\n"
    "2. If the sources do not answer the question, reply exactly with the REFUSAL text you are given.\n"
    "3. Eligibility verdicts are decided by the rule engine and given to you as facts. Explain them; never "
    "change a verdict or invent a criterion.\n"
    "4. Reply in the requested language and script. Use simple words a first-time reader understands. "
    "Keep scheme names in their official form.\n"
    "5. Be brief: at most 6 short sentences or bullets, then a 'Documents:' line if documents are known.\n"
    "6. Never ask for or repeat names, phone numbers, Aadhaar or bank numbers."
)


@dataclass
class Citation:
    n: int
    slug: str
    name: str
    section: str
    source_url: Optional[str]
    last_checked: Optional[str]
    verification: str
    snippet: str


@dataclass
class Answer:
    text: str
    lang: str
    citations: List[Citation] = field(default_factory=list)
    refused: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["disclaimer"] = DISCLAIMER.get(self.lang, DISCLAIMER["en"])
        return d


def citations_for(hits: List[Hit]) -> List[Citation]:
    return [Citation(n=i, slug=h.slug, name=h.name, section=h.section, source_url=h.source_url,
                     last_checked=h.last_checked, verification=h.verification, snippet=h.text[:280])
            for i, h in enumerate(hits, start=1)]


def build_prompt(question: str, hits: List[Hit], lang: str, verdicts: Optional[List[Dict[str, Any]]] = None,
                 profile: Optional[Dict[str, Any]] = None) -> str:
    sources = "\n\n".join(f"[{i}] ({h.name} — {h.section.replace('_', ' ')})\n{h.text}"
                          for i, h in enumerate(hits, start=1))
    parts = [f"Language: {LANG_NAMES.get(lang, 'English')}", f"REFUSAL text: {REFUSAL.get(lang, REFUSAL['en'])}"]
    if profile:
        parts.append("Citizen profile (eligibility fields only): " +
                     ", ".join(f"{k}={v}" for k, v in profile.items() if v is not None))
    if verdicts:
        lines = [f"- {v['name']}: {v['verdict'].upper()} ({'; '.join(v.get('reasons') or []) or 'no reasons'})"
                 + (f" — missing: {', '.join(v['missing'])}" if v.get("missing") else "")
                 + (f" — user must also confirm: {'; '.join(v['also_check'])}" if v.get("also_check") else "")
                 for v in verdicts[:8]]
        parts.append("Rule-engine verdicts (facts, do not change):\n" + "\n".join(lines))
    parts.append(f"Sources:\n{sources}")
    parts.append(f"Question: {question}")
    return "\n\n".join(parts)


def should_refuse(hits: List[Hit], top_score: float, threshold: float) -> bool:
    """No hits, or a reranker score under the threshold. threshold <= 0 disables the score check."""
    return not hits or (threshold > 0 and top_score < threshold)


# Sentence ends, including ones glued to the next sentence ("...process.Select the...").
_SENT = re.compile(r"(?<=[.?!।])\s*(?=[A-Z\u0900-\u097F])|(?<=[.?!।])\s+")


def extractive_answer(question: str, hits: List[Hit], lang: str) -> str:
    """Offline fallback (no LLM): best-overlapping sentences from the top chunks, each cited."""
    q = {t for t in tokenize(question) if t not in STOPWORDS}
    picked: List[str] = []
    for i, h in enumerate(hits[:3], start=1):
        body = h.text.split(": ", 1)[-1]
        sents = [s for s in _SENT.split(body) if 20 < len(s) <= 400] or [body[:300]]
        best = max(sents, key=lambda s: len(q & set(tokenize(s))))
        picked.append(f"{best.strip()} [{i}]")
    if not picked:
        return REFUSAL.get(lang, REFUSAL["en"])
    return " ".join(picked)


def generate(question: str, hits: List[Hit], lang: str, top_score: float, threshold: float,
             verdicts: Optional[List[Dict[str, Any]]] = None, profile: Optional[Dict[str, Any]] = None,
             llm: Optional[LLM] = None) -> Answer:
    llm = llm or get_llm()
    if should_refuse(hits, top_score, threshold):
        return Answer(REFUSAL.get(lang, REFUSAL["en"]), lang, [], refused=True)
    if llm.is_fake:
        text = extractive_answer(question, hits, lang)
    else:
        try:
            text = llm.complete(build_prompt(question, hits, lang, verdicts, profile), system=SYSTEM).strip()
        except RateLimited:
            text = extractive_answer(question, hits, lang)  # quota exhausted: quote the sources instead
    return finalize(text, hits, lang)


def stream_generate(question: str, hits: List[Hit], lang: str, top_score: float, threshold: float,
                    verdicts: Optional[List[Dict[str, Any]]] = None, profile: Optional[Dict[str, Any]] = None,
                    llm: Optional[LLM] = None) -> Iterator[str]:
    """Yield answer text pieces as the LLM produces them. Callers join them and call `finalize`."""
    llm = llm or get_llm()
    if should_refuse(hits, top_score, threshold):
        yield REFUSAL.get(lang, REFUSAL["en"])
        return
    if llm.is_fake:
        yield extractive_answer(question, hits, lang)
        return
    started = False
    try:
        for piece in llm.stream(build_prompt(question, hits, lang, verdicts, profile), system=SYSTEM):
            started = True
            yield piece
    except RateLimited:
        if not started:
            yield extractive_answer(question, hits, lang)  # quota exhausted: quote the sources instead


def is_refusal(text: str) -> bool:
    t = text.strip()
    return any(t.startswith(r[:40]) for r in REFUSAL.values())


def finalize(text: str, hits: List[Hit], lang: str) -> Answer:
    """Attach only the citations the text actually uses; drop out-of-range markers."""
    if is_refusal(text):
        return Answer(REFUSAL.get(lang, REFUSAL["en"]), lang, [], refused=True)
    cites = citations_for(hits)
    used = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text) if 1 <= int(n) <= len(hits)})
    text = re.sub(r"\[(\d+)\]", lambda m: m.group(0) if 1 <= int(m.group(1)) <= len(hits) else "", text)
    return Answer(text.strip(), lang, [c for c in cites if c.n in used], refused=False)
