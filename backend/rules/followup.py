"""Follow-up picker (contract + minimal version; Person B owns the real one).

Ask for the single missing field that would change the most 'maybe' results.
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional

from backend.rules.engine import Verdict

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


def pick_followup(verdicts: List[Verdict], lang: str = "en", asked: Optional[List[str]] = None) -> Optional[Dict[str, str]]:
    asked = set(asked or [])
    counts = Counter(f for v in verdicts if v.verdict == "maybe" for f in v.missing if f not in asked)
    if not counts:
        return None
    fld, _ = counts.most_common(1)[0]
    q = QUESTIONS.get(fld, {})
    return {"field": fld, "question": q.get(lang) or q.get("en") or f"Please tell me your {fld}."}
