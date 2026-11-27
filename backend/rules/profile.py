"""Profile extractor (contract + minimal version; Person B owns the real one).

extract_profile(text, existing) -> dict with only eligibility fields. Never stores names, phone numbers
or Aadhaar: anything that is not in PROFILE_FIELDS is dropped before it reaches the LLM or the session.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from backend.providers.llm import LLM, get_llm

PROFILE_FIELDS = ["state", "age", "gender", "occupation", "annual_income", "land_acres",
                  "social_category", "disability", "bpl"]

STATE_ALIASES = {
    "uttar pradesh": "Uttar Pradesh", "up": "Uttar Pradesh", "u.p.": "Uttar Pradesh", "उत्तर प्रदेश": "Uttar Pradesh",
    "यूपी": "Uttar Pradesh", "bihar": "Bihar", "बिहार": "Bihar", "rajasthan": "Rajasthan", "राजस्थान": "Rajasthan",
    "madhya pradesh": "Madhya Pradesh", "mp": "Madhya Pradesh", "मध्य प्रदेश": "Madhya Pradesh",
    "maharashtra": "Maharashtra", "महाराष्ट्र": "Maharashtra", "delhi": "Delhi", "दिल्ली": "Delhi",
    "gujarat": "Gujarat", "गुजरात": "Gujarat", "punjab": "Punjab", "पंजाब": "Punjab", "haryana": "Haryana",
    "हरियाणा": "Haryana", "west bengal": "West Bengal", "tamil nadu": "Tamil Nadu", "karnataka": "Karnataka",
    "kerala": "Kerala", "odisha": "Odisha", "assam": "Assam", "jharkhand": "Jharkhand",
    "uttarakhand": "Uttarakhand", "telangana": "Telangana", "andhra pradesh": "Andhra Pradesh",
}
SHORT_STATE_CODES = {"up", "u.p.", "mp"}
OCCUPATIONS = {
    "farmer": ["farmer", "kisan", "kisaan", "kheti", "किसान", "खेती"],
    "student": ["student", "studying", "padhai", "college", "छात्र", "छात्रा", "पढ़ाई", "vidyarthi"],
    "street_vendor": ["street vendor", "rehri", "thela", "ठेला", "रेहड़ी", "vendor"],
    "labourer": ["labour", "labor", "mazdoor", "majdoor", "मजदूर", "worker"],
    "unemployed": ["unemployed", "berozgar", "बेरोजगार", "no job", "naukri nahi"],
    "artisan": ["artisan", "weaver", "carpenter", "karigar", "कारीगर", "बुनकर"],
    "entrepreneur": ["business", "entrepreneur", "startup", "dukan", "दुकान", "vyapar", "व्यापार"],
}

_HINDI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _rules_extract(text: str) -> Dict[str, Any]:
    t = text.translate(_HINDI_DIGITS)
    low = t.lower()
    out: Dict[str, Any] = {}

    for alias, state in sorted(STATE_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if alias in SHORT_STATE_CODES:
            # "UP"/"MP" only as an uppercase standalone code, so "Stand-Up India" or "set up" don't match.
            hit = re.search(rf"(?<![\w\-]){re.escape(alias.upper())}(?![\w\-])", t)
        else:
            hit = re.search(rf"(?<![\w\-]){re.escape(alias)}(?![\w\-])", low)
        if hit:
            out["state"] = state
            break

    m = re.search(r"(\d{1,3})\s*(?:years?|yrs?|saal|sal|वर्ष|साल)", low) or \
        re.search(r"(?:age|umar|umr|उम्र|आयु)\D{0,6}(\d{1,3})", low)
    if m and 0 < int(m.group(1)) < 120:
        out["age"] = int(m.group(1))

    if re.search(r"\b(woman|female|girl|lady|mahila|ladki|widow|vidhwa|vidhva|vidhava|aurat)\b|महिला|लड़की|बेटी|विधवा", low):
        out["gender"] = "female"
    elif re.search(r"\b(man|male|boy|ladka|purush)\b|पुरुष|लड़का", low):
        out["gender"] = "male"

    for occ, kws in OCCUPATIONS.items():
        if any(k in low for k in kws):
            out["occupation"] = occ
            break

    inc = re.search(r"(?:income|aay|aamdani|kamai|आय|आमदनी|कमाई)\D{0,25}?([\d.,]+)\s*(lakh|lac|लाख|k|thousand|hazar|हजार)?", low)
    if inc:
        val = float(inc.group(1).replace(",", "") or 0)
        unit = inc.group(2) or ""
        if unit in ("lakh", "lac", "लाख"):
            val *= 100000
        elif unit in ("k", "thousand", "hazar", "हजार"):
            val *= 1000
        out["annual_income"] = int(val)

    land = re.search(r"([\d.]+)\s*(acre|acres|एकड़|bigha|बीघा)", low)
    if land:
        acres = float(land.group(1))
        if land.group(2) in ("bigha", "बीघा"):
            acres *= 0.62  # UP pucca bigha ≈ 0.62 acre
        out["land_acres"] = round(acres, 2)

    for cat in ("SC", "ST", "OBC"):
        if re.search(rf"\b{cat}\b", t):
            out["social_category"] = cat
            break
    else:
        if re.search(r"\b(general|gen)\b|सामान्य", low):
            out["social_category"] = "General"

    if re.search(r"disab|divyang|handicap|विकलांग|दिव्यांग", low):
        out["disability"] = True
    if re.search(r"\bbpl\b|below poverty|गरीबी रेखा", low):
        out["bpl"] = True
    return out


def _llm_extract(llm: LLM, text: str) -> Dict[str, Any]:
    prompt = (
        "Extract the citizen's eligibility profile from the message (Hindi, Hinglish or English).\n"
        f"Return JSON with only these keys when stated: {', '.join(PROFILE_FIELDS)}.\n"
        "state = Indian state name in English; age, annual_income (INR per year), land_acres = numbers; "
        "gender = male|female; social_category = SC|ST|OBC|General; disability, bpl = booleans; "
        "occupation = one word like farmer, student, labourer, street_vendor, artisan, entrepreneur.\n"
        "Omit anything not stated. Never include names, phone numbers or ID numbers.\n\n"
        f"Message: {text}"
    )
    data = llm.complete_json(prompt)
    return data if isinstance(data, dict) else {}


def extract_profile(text: str, existing: Optional[Dict[str, Any]] = None, llm: Optional[LLM] = None) -> Dict[str, Any]:
    """Merge fields found in `text` into `existing`. LLM first when available, regex as backstop."""
    llm = llm or get_llm()
    found = _rules_extract(text)
    if not llm.is_fake:
        try:
            found.update({k: v for k, v in _llm_extract(llm, text).items() if v not in (None, "")})
        except Exception:  # noqa: BLE001 - regex result still stands
            pass
    merged = dict(existing or {})
    merged.update({k: v for k, v in found.items() if k in PROFILE_FIELDS})
    return merged
