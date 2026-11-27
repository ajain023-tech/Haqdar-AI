"""Cheap language detection for en / hi (Devanagari) / hinglish (Hindi in Latin script)."""
from __future__ import annotations

import re

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_WORD = re.compile(r"[A-Za-z]+")

# Common Hindi words written in Latin script. A few hits => Hinglish.
HINGLISH_MARKERS = {
    "hai", "hain", "kya", "kaise", "kaun", "kitna", "kitne", "kitni", "mera", "meri", "mere", "mujhe",
    "main", "mai", "hum", "ham", "aap", "ke", "ki", "ka", "ko", "se", "me", "mein", "liye", "nahi",
    "nahin", "milega", "milegi", "milta", "yojana", "yojna", "sarkari", "kisan", "beti", "beta",
    "ghar", "paisa", "paise", "chahiye", "kar", "karna", "sakta", "sakti", "sakte", "wala", "wali",
    "bhi", "aur", "ya", "lakh", "saal", "umar", "umr", "gaon", "kaam", "naukri", "padhai", "batao",
    "bataiye", "kaunsi", "konsi", "kab", "kahan", "kyun", "toh", "tha", "thi", "hoon", "hu", "hun",
}


def detect_lang(text: str) -> str:
    """Return 'hi', 'hinglish' or 'en'."""
    if not text:
        return "en"
    deva = len(_DEVANAGARI.findall(text))
    letters = sum(1 for c in text if c.isalpha())
    if letters and deva / letters > 0.3:
        return "hi"
    words = [w.lower() for w in _WORD.findall(text)]
    if not words:
        return "en"
    hits = sum(1 for w in words if w in HINGLISH_MARKERS)
    if hits >= 2 and hits / len(words) >= 0.15:
        return "hinglish"
    return "en"


LANG_NAMES = {"hi": "Hindi (Devanagari script)", "hinglish": "Hinglish (Hindi in Latin script)", "en": "English"}


# Words that say nothing about *which* scheme or fact is meant. Ignored by the lexical reranker,
# the offline extractive answerer and scheme-name matching.
STOPWORDS = {
    "yojana", "yojna", "scheme", "schemes", "kya", "hai", "hain", "mein", "me", "ke", "ki", "ka", "ko", "se",
    "the", "and", "for", "what", "how", "is", "are", "of", "in", "to", "my", "i", "can", "get", "under", "a",
    "an", "about", "do", "does", "which", "who", "eligible", "eligibility", "kaise", "milega", "milegi", "liye",
    "kaun", "si", "tell", "please", "batao", "bataiye", "this", "that", "with", "it", "or", "on", "be", "aur",
    "से", "के", "की", "का", "है", "हैं", "में", "क्या", "योजना", "लिए", "कैसे", "मिलेगा", "मिलेगी", "को",
    "और", "कौन", "सी", "बताइए", "बताओ", "पात्र", "पात्रता",
}
