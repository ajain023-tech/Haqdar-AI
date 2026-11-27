"""Deterministic rule engine: profile x criteria -> eligible / maybe / no, with a reason per field.

evaluate(profile, schemes, lang="en") -> List[Verdict], sorted eligible > maybe > no.

Criteria JSON (null = no condition):
    states, age{min,max}, gender, occupation[], land_acres{min,max}, annual_income{min,max},
    social_category[], disability, bpl
A required field missing from the profile makes the scheme "maybe" and is listed in `missing`
(the follow-up picker asks for it). A scheme with no conditions at all is "maybe", never "eligible":
its criteria simply haven't been extracted yet. Reasons come back in the user's language.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

ORDER = {"eligible": 0, "maybe": 1, "no": 2}

# Occupations that satisfy a criterion for another (a fisherman or dairy farmer counts as a farmer
# for most agriculture schemes; construction workers are labourers).
OCCUPATION_IMPLIES = {
    "fisherman": {"farmer"}, "construction_worker": {"labourer"}, "weaver": {"artisan"},
    "street_vendor": {"self_employed"}, "artisan": {"self_employed"}, "entrepreneur": {"self_employed"},
}

T = {
    "en": {
        "state_only": "only for residents of {v}", "state_ok": "you live in {v}",
        "below_min": "{label} {v} is below the minimum {lim}", "above_max": "{label} {v} is above the maximum {lim}",
        "within": "{label} {v} is within the limit", "gender_only": "only for {v} applicants",
        "gender_ok": "{v} applicant", "one_of": "{label} must be {v}", "is": "{label}: {v}",
        "flag_only": "only for {v}", "flag_ok": "{v}", "no_rules": "eligibility rules not extracted yet; check the official page",
        "age": "Age", "annual_income": "Family income ₹", "land_acres": "Land (acres)", "occupation": "occupation",
        "social_category": "category", "disability": "persons with disability", "bpl": "BPL households",
        "female": "female", "male": "male",
    },
    "hi": {
        "state_only": "सिर्फ़ {v} के निवासियों के लिए", "state_ok": "आप {v} में रहते हैं",
        "below_min": "{label} {v} न्यूनतम सीमा {lim} से कम है", "above_max": "{label} {v} अधिकतम सीमा {lim} से ज़्यादा है",
        "within": "{label} {v} सीमा के अंदर है", "gender_only": "सिर्फ़ {v} आवेदकों के लिए",
        "gender_ok": "{v} आवेदक", "one_of": "{label} {v} होना चाहिए", "is": "{label}: {v}",
        "flag_only": "सिर्फ़ {v} के लिए", "flag_ok": "{v}", "no_rules": "पात्रता नियम अभी दर्ज नहीं हैं; आधिकारिक पेज देखें",
        "age": "उम्र", "annual_income": "पारिवारिक आय ₹", "land_acres": "ज़मीन (एकड़)", "occupation": "पेशा",
        "social_category": "वर्ग", "disability": "दिव्यांग व्यक्तियों", "bpl": "बीपीएल परिवारों",
        "female": "महिला", "male": "पुरुष",
    },
    "hinglish": {
        "state_only": "sirf {v} ke residents ke liye", "state_ok": "aap {v} mein rehte hain",
        "below_min": "{label} {v} minimum {lim} se kam hai", "above_max": "{label} {v} maximum {lim} se zyada hai",
        "within": "{label} {v} limit ke andar hai", "gender_only": "sirf {v} applicants ke liye",
        "gender_ok": "{v} applicant", "one_of": "{label} {v} hona chahiye", "is": "{label}: {v}",
        "flag_only": "sirf {v} ke liye", "flag_ok": "{v}", "no_rules": "eligibility rules abhi extract nahi hue; official page dekhein",
        "age": "Umar", "annual_income": "Family income ₹", "land_acres": "Zameen (acre)", "occupation": "kaam",
        "social_category": "category", "disability": "divyang logon", "bpl": "BPL parivaron",
        "female": "mahila", "male": "purush",
    },
}


@dataclass
class Verdict:
    scheme_id: int
    slug: str
    name: str
    verdict: str                      # eligible | maybe | no
    reasons: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _fmt(n: float) -> str:
    if float(n).is_integer():
        n = int(n)
        return f"{n:,}" if n >= 10000 else str(n)
    return f"{n:g}"


def _occupations(value: Any) -> set:
    vals = {value} if isinstance(value, str) else set(value or [])
    for v in list(vals):
        vals |= OCCUPATION_IMPLIES.get(v, set())
    return vals


def evaluate_one(profile: Dict[str, Any], scheme: Dict[str, Any], lang: str = "en") -> Verdict:
    t = T.get(lang, T["en"])
    c = scheme.get("criteria") or {}
    v = Verdict(scheme["id"], scheme["slug"], scheme["name"], "eligible")
    fails: List[str] = []
    ok: List[str] = []

    states = c.get("states") or ["ALL"]
    if "ALL" not in states:
        if not profile.get("state"):
            v.missing.append("state")
        elif profile["state"] not in states:
            fails.append(t["state_only"].format(v=", ".join(states)))
        else:
            ok.append(t["state_ok"].format(v=profile["state"]))

    for key in ("age", "annual_income", "land_acres"):
        rng = c.get(key)
        if not rng or (rng.get("min") is None and rng.get("max") is None):
            continue
        if profile.get(key) is None:
            v.missing.append(key)
            continue
        val = float(profile[key])
        lo, hi = rng.get("min"), rng.get("max")
        if lo is not None and val < lo:
            fails.append(t["below_min"].format(label=t[key], v=_fmt(val), lim=_fmt(lo)))
        elif hi is not None and val > hi:
            fails.append(t["above_max"].format(label=t[key], v=_fmt(val), lim=_fmt(hi)))
        else:
            ok.append(t["within"].format(label=t[key], v=_fmt(val)))

    gender = c.get("gender") or "any"
    if gender in ("male", "female"):
        if not profile.get("gender"):
            v.missing.append("gender")
        elif profile["gender"] != gender:
            fails.append(t["gender_only"].format(v=t[gender]))
        else:
            ok.append(t["gender_ok"].format(v=t[gender]))

    allowed = c.get("occupation")
    if allowed:
        if not profile.get("occupation"):
            v.missing.append("occupation")
        elif not (_occupations(profile["occupation"]) & set(allowed)):
            fails.append(t["one_of"].format(label=t["occupation"], v=" / ".join(allowed)))
        else:
            ok.append(t["is"].format(label=t["occupation"], v=profile["occupation"]))

    cats = c.get("social_category")
    if cats:
        if not profile.get("social_category"):
            v.missing.append("social_category")
        elif profile["social_category"] not in cats:
            fails.append(t["one_of"].format(label=t["social_category"], v=" / ".join(cats)))
        else:
            ok.append(t["is"].format(label=t["social_category"], v=profile["social_category"]))

    for flag in ("disability", "bpl"):
        if c.get(flag) is True:
            if profile.get(flag) is None:
                v.missing.append(flag)
            elif not profile[flag]:
                fails.append(t["flag_only"].format(v=t[flag]))
            else:
                ok.append(t["flag_ok"].format(v=t[flag]))

    if fails:
        v.verdict, v.reasons = "no", fails
    elif v.missing:
        v.verdict, v.reasons = "maybe", ok
    elif not ok:
        v.verdict, v.reasons = "maybe", [t["no_rules"]]
    else:
        v.reasons = ok
    return v


def evaluate(profile: Dict[str, Any], schemes: List[Dict[str, Any]], lang: Optional[str] = "en") -> List[Verdict]:
    out = [evaluate_one(profile, s, lang or "en") for s in schemes]
    # eligible first; within a verdict, schemes with more matched conditions (more specific) first
    out.sort(key=lambda x: (ORDER[x.verdict], len(x.missing), -len(x.reasons), x.name))
    return out
