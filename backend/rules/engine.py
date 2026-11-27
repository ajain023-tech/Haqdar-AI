"""Deterministic rule engine (contract + minimal version; Person B owns the real one).

evaluate(profile, schemes) -> List[Verdict], sorted eligible > maybe > no.
Criteria JSON: null = no condition. A required field missing from the profile => 'maybe' + that field is
listed in `missing`, which drives the follow-up question.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

ORDER = {"eligible": 0, "maybe": 1, "no": 2}


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


def _range_check(field_name: str, value: Any, rng: Dict[str, Any], label: str, unit: str = ""):
    lo, hi = rng.get("min"), rng.get("max")
    if lo is not None and value < lo:
        return False, f"{label} {value}{unit} is below the minimum {lo}{unit}"
    if hi is not None and value > hi:
        return False, f"{label} {value}{unit} is above the maximum {hi}{unit}"
    bounds = " to ".join(str(b) for b in (lo, hi) if b is not None)
    return True, f"{label} {value}{unit} is within {bounds}{unit}" if bounds else f"{label} ok"


def evaluate_one(profile: Dict[str, Any], scheme: Dict[str, Any]) -> Verdict:
    c = scheme.get("criteria") or {}
    v = Verdict(scheme["id"], scheme["slug"], scheme["name"], "eligible")
    fails: List[str] = []

    states = c.get("states") or ["ALL"]
    if "ALL" not in states:
        if not profile.get("state"):
            v.missing.append("state")
        elif profile["state"] not in states:
            fails.append(f"only for residents of {', '.join(states)}")
        else:
            v.reasons.append(f"resident of {profile['state']}")

    checks = [("age", "age", "Age", ""), ("annual_income", "annual_income", "Income ₹", ""),
              ("land_acres", "land_acres", "Land", " acres")]
    for crit_key, prof_key, label, unit in checks:
        rng = c.get(crit_key)
        if not rng:
            continue
        if profile.get(prof_key) is None:
            v.missing.append(prof_key)
            continue
        ok, why = _range_check(prof_key, float(profile[prof_key]), rng, label, unit)
        (v.reasons if ok else fails).append(why)

    gender = c.get("gender") or "any"
    if gender != "any":
        if not profile.get("gender"):
            v.missing.append("gender")
        elif profile["gender"] != gender:
            fails.append(f"only for {gender} applicants")
        else:
            v.reasons.append(f"{gender} applicant")

    for crit_key, label in (("occupation", "occupation"), ("social_category", "social category")):
        allowed = c.get(crit_key)
        if not allowed:
            continue
        val = profile.get(crit_key)
        if val is None:
            v.missing.append(crit_key)
        elif val not in allowed:
            fails.append(f"{label} must be one of {', '.join(allowed)}")
        else:
            v.reasons.append(f"{label}: {val}")

    for flag, label in (("disability", "person with disability"), ("bpl", "BPL household")):
        if c.get(flag) is True:
            if profile.get(flag) is None:
                v.missing.append(flag)
            elif not profile[flag]:
                fails.append(f"only for {label}")
            else:
                v.reasons.append(label)

    if fails:
        v.verdict, v.reasons = "no", fails
    elif v.missing:
        v.verdict = "maybe"
    elif not v.reasons:
        # No condition was checked at all: the criteria haven't been extracted yet. Never call that "eligible".
        v.verdict, v.reasons = "maybe", ["eligibility rules not extracted yet; check the official page"]
    return v


def evaluate(profile: Dict[str, Any], schemes: List[Dict[str, Any]]) -> List[Verdict]:
    out = [evaluate_one(profile, s) for s in schemes]
    out.sort(key=lambda x: (ORDER[x.verdict], -len(x.reasons), x.name))
    return out
