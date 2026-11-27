"""Follow-up picker: ask for the one missing field that would change the most results.

Each "maybe" scheme contributes 1/len(missing) to every field it is missing, so a field that is the
*only* thing standing between the user and a verdict counts fully, while a field that is one of five
unknowns counts a fifth. Fields already asked in this session are skipped. Ties prefer cheap questions.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from backend.rules.engine import Verdict

# Easier questions first when scores tie.
EASE = ["state", "age", "gender", "occupation", "bpl", "annual_income", "social_category", "land_acres",
        "disability"]

QUESTIONS: Dict[str, Dict[str, str]] = {
    "state": {"en": "Which state do you live in?", "hi": "आप किस राज्य में रहते हैं?",
              "hinglish": "Aap kis state mein rehte hain?"},
    "age": {"en": "How old are you?", "hi": "आपकी उम्र कितनी है?", "hinglish": "Aapki umar kitni hai?"},
    "gender": {"en": "Are you male or female?", "hi": "आप पुरुष हैं या महिला?",
               "hinglish": "Aap purush hain ya mahila?"},
    "annual_income": {"en": "What is your family's yearly income, roughly?",
                      "hi": "आपके परिवार की सालाना आय लगभग कितनी है?",
                      "hinglish": "Aapke parivar ki saalana income lagbhag kitni hai?"},
    "land_acres": {"en": "How much farm land do you own (in acres or bigha)?",
                   "hi": "आपके पास कितनी खेती की ज़मीन है (एकड़ या बीघा में)?",
                   "hinglish": "Aapke paas kitni kheti ki zameen hai (acre ya bigha mein)?"},
    "occupation": {"en": "What work do you do?", "hi": "आप क्या काम करते हैं?", "hinglish": "Aap kya kaam karte hain?"},
    "social_category": {"en": "Which category do you belong to: General, OBC, SC or ST?",
                        "hi": "आप किस वर्ग से हैं: सामान्य, ओबीसी, एससी या एसटी?",
                        "hinglish": "Aap kis category se hain: General, OBC, SC ya ST?"},
    "disability": {"en": "Do you have a disability certificate?", "hi": "क्या आपके पास विकलांगता प्रमाणपत्र है?",
                   "hinglish": "Kya aapke paas disability certificate hai?"},
    "bpl": {"en": "Does your family have a BPL card?", "hi": "क्या आपके परिवार के पास बीपीएल कार्ड है?",
            "hinglish": "Kya aapke parivar ke paas BPL card hai?"},
}
# Quick-reply chips the UI can show under the question.
OPTIONS: Dict[str, List[str]] = {
    "gender": ["female", "male"], "bpl": ["yes", "no"], "disability": ["yes", "no"],
    "social_category": ["General", "OBC", "SC", "ST"],
}


def score_fields(verdicts: List[Verdict], asked: Optional[List[str]] = None) -> Dict[str, float]:
    asked = set(asked or [])
    scores: Dict[str, float] = defaultdict(float)
    for v in verdicts:
        if v.verdict != "maybe" or not v.missing:
            continue
        for f in v.missing:
            if f not in asked:
                scores[f] += 1.0 / len(v.missing)
    return dict(scores)


def pick_followup(verdicts: List[Verdict], lang: str = "en", asked: Optional[List[str]] = None) -> Optional[Dict]:
    scores = score_fields(verdicts, asked)
    if not scores:
        return None
    fld = max(scores, key=lambda f: (round(scores[f], 6), -EASE.index(f) if f in EASE else -99))
    q = QUESTIONS.get(fld, {})
    out = {"field": fld, "question": q.get(lang) or q.get("en") or f"Please tell me your {fld}.",
           "affects": int(round(sum(1 for v in verdicts if v.verdict == "maybe" and fld in v.missing)))}
    if fld in OPTIONS:
        out["options"] = OPTIONS[fld]
    return out
