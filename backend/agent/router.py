"""Router: "find schemes for me" vs "question about scheme X", plus scheme-name resolution.

Resolution maps a mention ("PM Kisan", "पीएम किसान", "ujjwala") to scheme ids so retrieval can be
filtered to that scheme. A question that *names* a scheme we cannot resolve is flagged, so the agent
can answer "I couldn't find this in official sources" instead of describing a similar scheme.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from backend.core.lang import STOPWORDS
from backend.providers.llm import LLM, get_fast_llm
from backend.rag.store import Store, tokenize

# Hindi / Hinglish / colloquial names for the most-asked schemes -> myScheme slug.
ALIASES: Dict[str, List[str]] = {
    "pm-kisan": ["pm kisan", "pm-kisan", "pmkisan", "kisan samman nidhi", "पीएम किसान", "किसान सम्मान निधि",
                 "पीएम-किसान", "6000 wali yojana"],
    "pmuy": ["ujjwala", "ujjawala", "उज्ज्वला", "free gas connection", "मुफ्त गैस"],
    "pmay-g": ["pmay-g", "pmay gramin", "awas yojana gramin", "gramin awas", "ग्रामीण आवास", "आवास योजना ग्रामीण"],
    "pmay-u": ["pmay-u", "pmay urban", "awas yojana urban", "shahri awas", "शहरी आवास"],
    "ssy": ["sukanya", "sukanya samriddhi", "सुकन्या", "सुकन्या समृद्धि"],
    "apy": ["atal pension", "अटल पेंशन"],
    "pmjjby": ["jeevan jyoti", "pmjjby", "जीवन ज्योति"],
    "pmsby": ["suraksha bima", "pmsby", "सुरक्षा बीमा"],
    "pmmy": ["mudra", "मुद्रा"],
    "sui": ["stand up india", "stand-up india", "स्टैंड अप इंडिया", "स्टैंड-अप इंडिया"],
    "kcc": ["kisan credit card", "kcc", "किसान क्रेडिट कार्ड"],
    "pmfby": ["fasal bima", "crop insurance", "pmfby", "फसल बीमा"],
    "mgnrega": ["mgnrega", "nrega", "manrega", "मनरेगा", "नरेगा", "job card"],
    "jsy": ["janani suraksha", "जननी सुरक्षा"],
    "pmmvy": ["matru vandana", "मातृ वंदना", "pmmvy"],
    "ignwps": ["widow pension", "vidhwa pension", "विधवा पेंशन"],
    "igndps": ["disability pension", "viklang pension", "divyang pension", "विकलांग पेंशन", "दिव्यांग पेंशन"],
    "pmegp": ["pmegp", "rozgar srijan", "रोजगार सृजन"],
    "ksy": ["kanya sumangala", "कन्या सुमंगला"],
    "pmjdy": ["jan dhan", "jandhan", "जन धन", "जनधन"],
    "pm-svanidhi": ["svanidhi", "swanidhi", "स्वनिधि", "street vendor loan", "रेहड़ी"],
    "nfbsup": ["family benefit", "parivarik labh", "पारिवारिक लाभ"],
}

NAME_GENERIC = STOPWORDS | {"pradhan", "mantri", "pm", "national", "scheme", "yojana", "yojna", "scheme", "india",
                            "bharat", "rashtriya", "mukhyamantri", "cm", "state", "government", "sarkari",
                            "प्रधानमंत्री", "प्रधान", "मंत्री", "राष्ट्रीय", "मुख्यमंत्री", "सरकारी", "पीएम", "free",
                            "मुफ्त", "nidhi", "yojana", "programme", "program", "mission", "abhiyan",
                            # category words: "a pension scheme" names no particular scheme
                            "pension", "loan", "insurance", "housing", "scholarship", "savings", "subsidy",
                            "welfare", "central", "new", "पेंशन", "लोन", "बीमा", "आवास", "छात्रवृत्ति"}
_NAMED = re.compile(r"((?:[\wऀ-ॿ\-]+\s+){1,4})(yojana|yojna|scheme|mission|abhiyan|योजना|मिशन|अभियान)(?![\w\u0900-\u097F])",
                    re.I | re.U)
_LEAD_GENERIC = {"kaun", "konsi", "kaunsi", "koi", "kis", "si", "sarkari", "which", "what", "any", "is", "this",
                 "the", "a", "kya", "mere", "meri", "liye", "government", "कौन", "सी", "कोई", "किस", "सरकारी",
                 "मेरे", "लिए", "क्या", "यह", "इस", "best", "new", "nayi", "नई", "for", "me", "se", "ke", "ki",
                 "of", "about", "in", "under", "batao", "bataiye", "बताइए", "बताओ", "wali", "wala", "vali",
                 "vala", "वाली", "वाला", "waali", "waala", "ka", "का", "की", "के"}

FIND_CUES = re.compile(
    r"which schemes?|what schemes?|schemes? for me|am i eligible|eligible for|can i get|kaun ?si|konsi|kaunsi|"
    r"kya milega|kya mil sakta|milegi|mil sakti|mere liye|मेरे लिए|कौन सी|कौन-सी|कौनसी|क्या मिल|मिलेगी|मिलेगा|"
    r"\bi am\b|\bi'm\b|main ek|mai ek|मैं एक|meri umar|my age|मेरी उम्र|my income|meri income|मेरी आय|"
    r"\d+\s*(?:years?|saal|साल|वर्ष)\b|\bfarmer\b|किसान हूँ|kisan hu",
    re.I | re.U)


@dataclass
class Route:
    intent: str                              # find_schemes | scheme_question
    scheme_ids: List[int] = field(default_factory=list)
    named: Optional[str] = None              # scheme-like phrase in the question
    named_resolved: bool = True


class SchemeIndex:
    """Name/alias index over active schemes, refreshed every few minutes."""
    TTL = 300

    def __init__(self, store: Store) -> None:
        self.store = store
        self._built = 0.0
        self._names: List[Tuple[str, int]] = []         # (alias phrase, scheme id)
        self._tokens: Dict[int, set] = {}               # scheme id -> distinctive name tokens
        self._slug_to_id: Dict[str, int] = {}

    def _build(self) -> None:
        names: List[Tuple[str, int]] = []
        tokens: Dict[int, set] = {}
        slug_to_id: Dict[str, int] = {}
        for s in self.store.list_schemes():
            sid, name = s["id"], s["name"]
            slug_to_id[s["slug"]] = sid
            names.append((name.lower(), sid))
            if s.get("name_hi"):
                names.append((s["name_hi"].lower(), sid))
            for acr in re.findall(r"\(([^)]{2,25})\)", name):
                names.append((acr.lower(), sid))
            if len(s["slug"]) >= 3:
                names.append((s["slug"].lower(), sid))
            tokens[sid] = {t for t in tokenize(name) if t not in NAME_GENERIC and len(t) > 2}
        for slug, aliases in ALIASES.items():
            if slug in slug_to_id:
                names.extend((a.lower(), slug_to_id[slug]) for a in aliases)
        names.sort(key=lambda x: -len(x[0]))
        self._names, self._tokens, self._slug_to_id = names, tokens, slug_to_id
        self._built = time.time()

    def _fresh(self) -> None:
        if time.time() - self._built > self.TTL or not self._names:
            self._build()

    def resolve(self, text: str) -> List[int]:
        self._fresh()
        low = f" {text.lower()} "
        found: List[int] = []
        for phrase, sid in self._names:
            if len(phrase) < 3 or sid in found:
                continue
            if re.search(rf"(?<![\wऀ-ॿ]){re.escape(phrase)}(?![\wऀ-ॿ])", low):
                found.append(sid)
        return found[:3]

    def name_tokens(self, sid: int) -> set:
        self._fresh()
        return self._tokens.get(sid, set())


def named_phrase(text: str) -> Optional[str]:
    """The words right before 'yojana/scheme/योजना', up to the first generic word.

    'What is PM Free Smartphone Yojana?' -> 'PM Free Smartphone Yojana'
    '6000 रुपये किस योजना में'            -> None   ('किस' is generic, so nothing is named)
    'kaun si yojana milegi'              -> None
    """
    if not text:
        return None
    for m in _NAMED.finditer(text):
        words: List[str] = []
        for w in reversed(m.group(1).split()):
            if w.lower() in _LEAD_GENERIC or re.fullmatch(r"[\d,.]+", w):
                break
            words.insert(0, w)
        if any(w.lower() not in NAME_GENERIC for w in words):
            return " ".join(words + [m.group(2)])
    return None


def route(message: str, index: SchemeIndex, has_pending_followup: bool = False,
          llm: Optional[LLM] = None) -> Route:
    ids = index.resolve(message)
    named = named_phrase(message)
    if ids:
        return Route("scheme_question", ids, named, True)
    if named:
        return Route("scheme_question", [], named, False)
    if FIND_CUES.search(message):
        return Route("find_schemes")
    if has_pending_followup and len(message.split()) <= 8:
        return Route("find_schemes")  # short reply to our follow-up question
    llm = llm or get_fast_llm()
    if not llm.is_fake:
        try:
            data = llm.complete_json(
                "Classify the message to an Indian government-schemes assistant. Return JSON "
                '{"intent": "find_schemes" | "scheme_question"}. find_schemes = the user describes their '
                "situation or asks what they can get; scheme_question = a question about a scheme or process.\n\n"
                f"Message: {message}")
            if isinstance(data, dict) and data.get("intent") in ("find_schemes", "scheme_question"):
                return Route(data["intent"])
        except Exception:  # noqa: BLE001
            pass
    return Route("scheme_question")
