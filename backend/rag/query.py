"""Cross-lingual query rewriting: Hindi / Hinglish question -> short English search query.

Most official text is English, so a Hindi question is searched twice (original + English rewrite) and
the ranked lists are fused with RRF. With an LLM the rewrite also transliterates scheme names
("पीएम फ्री लैपटॉप योजना" -> "PM Free Laptop Yojana"); offline a welfare-vocabulary glossary is used.
"""
from __future__ import annotations

import re
from typing import Optional

from backend.providers.llm import LLM, get_fast_llm

# Common welfare vocabulary (Devanagari + Hinglish) -> English terms used on myScheme pages.
GLOSSARY = {
    # people
    "किसान": "farmer", "kisan": "farmer", "kisaan": "farmer", "किसानों": "farmers",
    "महिला": "women", "महिलाओं": "women", "mahila": "women", "औरत": "women",
    "बेटी": "girl child daughter", "beti": "girl child daughter", "लड़की": "girl", "ladki": "girl", "कन्या": "girl child",
    "विधवा": "widow", "vidhwa": "widow", "पति की मृत्यु": "widow husband death",
    "विकलांग": "disability disabled", "दिव्यांग": "disability divyang", "viklang": "disability",
    "बुजुर्ग": "old age senior citizen", "वृद्ध": "old age", "छात्र": "student", "छात्रा": "student",
    "मज़दूर": "worker labourer", "मजदूर": "worker labourer", "mazdoor": "worker labourer",
    "मज़दूरों": "workers labourers", "असंगठित": "unorganised sector", "गर्भवती": "pregnant women",
    "माँ": "mother", "बच्चे": "child", "ठेला": "street vendor", "रेहड़ी": "street vendor", "thela": "street vendor",
    "पटरी": "street vendor", "गरीब": "poor BPL", "garib": "poor BPL", "बीपीएल": "BPL below poverty line",
    # things
    "घर": "house housing", "ghar": "house housing", "पक्का": "pucca house", "pakka": "pucca house",
    "kachcha": "kutcha house", "कच्चा": "kutcha house", "आवास": "housing awas", "शहर": "urban city",
    "शहरी": "urban", "shahri": "urban", "गाँव": "rural village gramin", "gaon": "rural village", "ग्रामीण": "rural gramin",
    "पेंशन": "pension", "pension": "pension", "बीमा": "insurance bima", "bima": "insurance",
    "दुर्घटना": "accident", "जीवन": "life", "लोन": "loan", "loan": "loan", "ऋण": "loan credit", "उधार": "credit loan",
    "गारंटी": "collateral guarantee", "बिना गारंटी": "collateral free", "ब्याज": "interest", "सब्सिडी": "subsidy",
    "छूट": "subsidy", "खाता": "account", "बैंक": "bank", "बचत": "savings", "ज़ीरो बैलेंस": "zero balance",
    "ओवरड्राफ्ट": "overdraft", "गैस": "LPG gas", "एलपीजी": "LPG", "सिलेंडर": "cylinder", "कनेक्शन": "connection",
    "फसल": "crop", "fasal": "crop", "बारिश": "rain natural calamity", "सूखा": "drought", "सूखे": "drought",
    "मुआवज़ा": "compensation claim", "खेती": "agriculture farming", "kheti": "agriculture farming",
    "रोज़गार": "employment", "रोजगार": "employment", "rozgar": "employment", "काम": "work employment",
    "kaam": "work", "100 दिन": "100 days employment guarantee", "100 din": "100 days employment guarantee",
    "व्यापार": "business", "dukan": "shop business", "दुकान": "shop business", "उद्योग": "industry enterprise unit",
    "पढ़ाई": "education", "padhai": "education", "शादी": "marriage", "जन्म": "birth", "प्रसव": "delivery maternity",
    "पैसे": "financial assistance amount", "paise": "money amount", "मदद": "assistance", "सहायता": "assistance",
    "दस्तावेज़": "documents", "documents": "documents", "आवेदन": "apply application", "पात्रता": "eligibility",
    "उम्र": "age", "आय": "income", "प्रीमियम": "premium", "किस्त": "instalment", "kist": "instalment",
    "कार्ड": "card", "क्रेडिट": "credit", "सालाना": "per year annual", "महीने": "monthly",
}
_WS = re.compile(r"\s+")


def glossary_rewrite(question: str) -> Optional[str]:
    low = question.lower()
    terms = []
    for src, dst in sorted(GLOSSARY.items(), key=lambda kv: -len(kv[0])):
        if src in low:
            terms.append(dst)
            low = low.replace(src, " ")
    digits = re.findall(r"\d[\d,]*", question)
    out = _WS.sub(" ", " ".join(terms + digits)).strip()
    return out or None


def rewrite_query(question: str, lang: str, llm: Optional[LLM] = None) -> Optional[str]:
    """English search query for a hi / hinglish question, or None for English questions."""
    if lang == "en":
        return None
    llm = llm or get_fast_llm()
    if not llm.is_fake:
        try:
            text = llm.complete(
                "Rewrite this question as a short English search query for Indian government scheme documents. "
                "Transliterate scheme names to their official English names. Return only the query.\n\n"
                f"Question: {question}", temperature=0.0).strip()
            if text:
                return text.splitlines()[0][:300]
        except Exception:  # noqa: BLE001 - glossary backstop
            pass
    return glossary_rewrite(question)
