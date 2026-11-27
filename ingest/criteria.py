"""Criteria extraction: eligibility text -> the frozen criteria JSON.

    {"states": ["ALL"], "age": {"min": 18, "max": 40}, "gender": "any", "occupation": ["farmer"],
     "land_acres": {"max": 5}, "annual_income": {"max": 200000}, "social_category": ["SC", "ST"],
     "disability": null, "bpl": null}

null = no condition. The LLM reads the eligibility + exclusions sections and returns JSON; the result is
validated and normalised so the rule engine never sees a malformed value. Without an LLM (or if it fails)
the regex bootstrap is used. Everything extracted here is "auto" until a person verifies it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.providers.llm import LLM, get_llm
from scripts.load_hf_dataset import bootstrap_criteria

OCCUPATIONS = ["farmer", "student", "street_vendor", "labourer", "artisan", "entrepreneur", "unemployed",
               "fisherman", "construction_worker", "weaver", "salaried", "self_employed"]
CATEGORIES = ["SC", "ST", "OBC", "General", "EWS", "Minority"]
KEYS = ["states", "age", "gender", "occupation", "land_acres", "annual_income", "social_category",
        "disability", "bpl"]

PROMPT = """You convert Indian government scheme eligibility rules into JSON for a rule engine.
Use ONLY conditions the text states. Use null when the text sets no condition on that field.

Fields:
- states: list of Indian state names in English, or ["ALL"] for a central scheme open everywhere
- age: {{"min": int|null, "max": int|null}} in years, or null
- gender: "male" | "female" | "any"
- occupation: list from {occupations}, or null
- land_acres: {{"min": number|null, "max": number|null}} (convert hectares x 2.47), or null
- annual_income: {{"min": int|null, "max": int|null}} family income in rupees per year (1 lakh = 100000), or null
- social_category: list from {categories}, or null
- disability: true if only for persons with disability, else null
- bpl: true if only for BPL / below-poverty-line households, else null

Scheme state: {state}
Eligibility:
{eligibility}

Exclusions:
{exclusions}

Return only the JSON object."""


def _range(v: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(v, dict):
        return None
    out = {}
    for k in ("min", "max"):
        x = v.get(k)
        if isinstance(x, (int, float)) and x >= 0:
            out[k] = int(x) if float(x).is_integer() else round(float(x), 2)
    return out or None


def _choices(v: Any, allowed: List[str]) -> Optional[List[str]]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return None
    lookup = {a.lower(): a for a in allowed}
    out = [lookup[x.lower().replace(" ", "_")] for x in v if isinstance(x, str) and x.lower().replace(" ", "_") in lookup]
    return out or None


def normalise(raw: Dict[str, Any], state: str) -> Dict[str, Any]:
    """Validate an LLM result into the criteria schema; unknown keys and bad values are dropped."""
    states = raw.get("states")
    if not isinstance(states, list) or not states or not all(isinstance(s, str) for s in states):
        states = [state] if state != "ALL" else ["ALL"]
    if state != "ALL":
        states = [state]  # a state scheme is for that state, whatever the text implies
    gender = raw.get("gender") if raw.get("gender") in ("male", "female") else "any"
    return {
        "states": states,
        "age": _range(raw.get("age")),
        "gender": gender,
        "occupation": _choices(raw.get("occupation"), OCCUPATIONS),
        "land_acres": _range(raw.get("land_acres")),
        "annual_income": _range(raw.get("annual_income")),
        "social_category": _choices(raw.get("social_category"), CATEGORIES),
        "disability": True if raw.get("disability") is True else None,
        "bpl": True if raw.get("bpl") is True else None,
    }


def extract_criteria(sections: Dict[str, str], state: str = "ALL", llm: Optional[LLM] = None) -> Dict[str, Any]:
    """Returns criteria plus "_extracted_by": "llm" | "regex" (ignored by the rule engine)."""
    llm = llm or get_llm()
    fallback = bootstrap_criteria(sections, state)
    elig = (sections.get("eligibility") or "")[:4000]
    if llm.is_fake or not elig:
        return {**fallback, "_extracted_by": "regex"}
    try:
        data = llm.complete_json(PROMPT.format(occupations=OCCUPATIONS, categories=CATEGORIES, state=state,
                                               eligibility=elig, exclusions=(sections.get("exclusions") or "")[:1500]))
        if isinstance(data, dict):
            return {**normalise(data, state), "_extracted_by": "llm"}
    except Exception:  # noqa: BLE001 - regex result still stands
        pass
    return {**fallback, "_extracted_by": "regex"}


def diff_criteria(a: Dict[str, Any], b: Dict[str, Any]) -> List[str]:
    """Fields where two extractions disagree (used to flag page vs guideline-PDF conflicts)."""
    return [k for k in KEYS if (a or {}).get(k) != (b or {}).get(k)]

