"""Self-check: every factual claim in the answer must map to a retrieved chunk.

Claims are the answer's sentences/bullets. Each is checked against the chunk(s) it cites:
an LLM judge when available, lexical support otherwise. The result also gives the faithfulness score
used in the eval (supported claims / all claims).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from backend.providers.llm import LLM, get_fast_llm
from backend.rag.answer import is_refusal
from backend.rag.store import Hit, tokenize

# A claim ends at sentence punctuation *plus any citation markers that follow it* ("... year. [1][2] Next"),
# or at a line break / bullet.
_BOUNDARY = re.compile(r"[.?!।]+(?:\s*\[\d+\])*(?:\s+|$)|\n+\s*[-*•]?\s*")
_CITE = re.compile(r"\[(\d+)\]")
_NUM = re.compile(r"\d[\d,]*")
# Sentences that carry no checkable fact (headers, offers to help) are skipped.
_NON_CLAIM = re.compile(r"^(documents?|दस्तावेज़?|kaagzat|note|नोट)\s*:?\s*$", re.I)
LEXICAL_SUPPORT = 0.5


@dataclass
class ClaimCheck:
    claim: str
    cited: List[int]
    supported: bool
    reason: str = ""


@dataclass
class SelfCheckResult:
    ok: bool
    faithfulness: float
    claims: List[ClaimCheck] = field(default_factory=list)

    @property
    def unsupported(self) -> List[ClaimCheck]:
        return [c for c in self.claims if not c.supported]

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "faithfulness": self.faithfulness, "claims": [asdict(c) for c in self.claims]}


def split_claims(text: str) -> List[str]:
    parts: List[str] = []
    pos = 0
    for m in _BOUNDARY.finditer(text or ""):
        parts.append(text[pos:m.end()].strip(" -*•\n"))
        pos = m.end()
    parts.append((text or "")[pos:].strip(" -*•\n"))
    return [p for p in parts if len(tokenize(_CITE.sub("", p))) >= 3 and not _NON_CLAIM.match(p)]


def _lexical_supported(claim: str, chunks: List[str]) -> bool:
    words = {w for w in tokenize(_CITE.sub("", claim)) if len(w) > 2}
    if not words or not chunks:
        return False
    pool = set()
    for c in chunks:
        pool |= set(tokenize(c))
    # numbers must match exactly: a wrong amount is the costliest hallucination
    nums = {n.replace(",", "") for n in _NUM.findall(_CITE.sub("", claim))}
    chunk_nums = {n.replace(",", "") for c in chunks for n in _NUM.findall(c)}
    if not nums <= chunk_nums:
        return False
    return len(words & pool) / len(words) >= LEXICAL_SUPPORT


def _llm_judge(llm: LLM, claims: List[str], hits: List[Hit]) -> Optional[List[bool]]:
    sources = "\n\n".join(f"[{i}] {h.text[:900]}" for i, h in enumerate(hits, start=1))
    listing = "\n".join(f"{i}. {c}" for i, c in enumerate(claims, start=1))
    prompt = (
        "For each numbered claim, decide if it is fully supported by the sources (same facts, numbers, "
        "conditions). A claim in Hindi or Hinglish is supported if a source states the same fact in any language.\n"
        'Return JSON: {"supported": [true|false, ...]} with one entry per claim, in order.\n\n'
        f"Sources:\n{sources}\n\nClaims:\n{listing}"
    )
    data = llm.complete_json(prompt)
    if isinstance(data, dict) and isinstance(data.get("supported"), list) and len(data["supported"]) == len(claims):
        return [bool(x) for x in data["supported"]]
    return None


def check(answer_text: str, hits: List[Hit], llm: Optional[LLM] = None) -> SelfCheckResult:
    llm = llm or get_fast_llm()
    if is_refusal(answer_text):
        return SelfCheckResult(ok=True, faithfulness=1.0)
    claims = split_claims(answer_text)
    if not claims:
        return SelfCheckResult(ok=True, faithfulness=1.0)

    checks: List[ClaimCheck] = []
    judged = None if llm.is_fake else _safe_judge(llm, claims, hits)
    for i, claim in enumerate(claims):
        cited = [int(n) for n in _CITE.findall(claim) if 1 <= int(n) <= len(hits)]
        if not cited:
            checks.append(ClaimCheck(claim, [], False, "no citation"))
            continue
        if judged is not None:
            checks.append(ClaimCheck(claim, cited, judged[i], "llm judge"))
        else:
            ok = _lexical_supported(claim, [hits[n - 1].text for n in cited])
            checks.append(ClaimCheck(claim, cited, ok, "lexical"))

    supported = sum(1 for c in checks if c.supported)
    faith = supported / len(checks)
    return SelfCheckResult(ok=supported == len(checks), faithfulness=round(faith, 3), claims=checks)


def _safe_judge(llm: LLM, claims: List[str], hits: List[Hit]) -> Optional[List[bool]]:
    try:
        return _llm_judge(llm, claims, hits)
    except Exception:  # noqa: BLE001 - fall back to lexical support
        return None


def strip_unsupported(answer_text: str, result: SelfCheckResult) -> str:
    """Remove unsupported claims from the answer text (used after the one re-retrieval also fails)."""
    out = answer_text
    for c in result.unsupported:
        out = out.replace(c.claim, "")
    out = re.sub(r"\n\s*[-*•]\s*\n", "\n", out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()
