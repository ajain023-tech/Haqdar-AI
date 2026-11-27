import os

import pytest

# Force offline providers before anything reads settings.
for var in ("GEMINI_API_KEY", "DATABASE_URL", "REDIS_URL", "RERANKER_URL", "LANGFUSE_PUBLIC_KEY",
            "LANGFUSE_SECRET_KEY", "SARVAM_API_KEY"):
    os.environ[var] = ""
os.environ["ADMIN_TOKEN"] = "test-admin"
os.environ["RATE_LIMIT_PER_MIN"] = "1000"

from backend.agent.flow import Agent  # noqa: E402
from backend.core.cache import MemoryCache, set_cache  # noqa: E402
from backend.rag.indexer import index_scheme  # noqa: E402
from backend.rag.store import MemoryStore, set_store  # noqa: E402

SCHEMES = [
    ({"slug": "pm-kisan", "name": "Pradhan Mantri Kisan Samman Nidhi", "state": "ALL", "category": "agriculture",
      "ministry": "Ministry Of Agriculture and Farmers Welfare", "source_url": "https://www.myscheme.gov.in/schemes/pm-kisan",
      "documents_required": ["Aadhaar Card", "Land records", "Bank account details"],
      "criteria": {"states": ["ALL"], "occupation": ["farmer"], "land_acres": None}},
     {"details": "PM-KISAN is a central sector scheme that provides income support to all landholding farmer families.",
      "benefits": "Financial benefit of Rs 6000 per year is given in three equal instalments of Rs 2000 each "
                  "directly into the bank accounts of farmer families.",
      "eligibility": "All landholding farmer families with cultivable land in their names are eligible.",
      "exclusions": "Institutional landholders, income tax payers and serving government employees are excluded.",
      "documents_required": "Aadhaar Card. Land holding papers. Bank account details."}),
    ({"slug": "pmuy", "name": "Pradhan Mantri Ujjwala Yojana", "state": "ALL", "category": "women_child",
      "source_url": "https://www.myscheme.gov.in/schemes/pmuy", "documents_required": ["Aadhaar", "Ration Card"],
      "criteria": {"states": ["ALL"], "gender": "female", "age": {"min": 18}, "bpl": True}},
     {"details": "Pradhan Mantri Ujjwala Yojana provides deposit free LPG connections to women from poor households.",
      "eligibility": "The applicant must be a woman of at least 18 years of age from a BPL household. "
                     "A male member cannot apply for the scheme.",
      "documents_required": "KYC form. Aadhaar card. Ration card issued by the state. Bank account number and IFSC."}),
    ({"slug": "ksy", "name": "Kanya Sumangala Yojana", "state": "Uttar Pradesh", "category": "women_child",
      "source_url": "https://www.myscheme.gov.in/schemes/ksy",
      "criteria": {"states": ["Uttar Pradesh"], "gender": "female", "annual_income": {"max": 300000}}},
     {"details": "Kanya Sumangala Yojana of the Uttar Pradesh government supports the girl child.",
      "benefits": "A total of Rs 25000 is given to the girl child in six phases from birth to graduation.",
      "eligibility": "The family should be a resident of Uttar Pradesh with annual family income up to Rs 3 lakh."}),
    ({"slug": "ignwps", "name": "Indira Gandhi National Widow Pension Scheme", "state": "ALL", "category": "pension",
      "source_url": "https://www.myscheme.gov.in/schemes/ignwps",
      "criteria": {"states": ["ALL"], "gender": "female", "age": {"min": 40, "max": 79}, "bpl": True}},
     {"details": "Monthly pension for widows from BPL households.",
      "benefits": "A monthly pension of Rs 300 is given to widows aged 40 to 79 years.",
      "eligibility": "The applicant should be a widow aged between 40 and 79 years belonging to a BPL household."}),
    ({"slug": "sui", "name": "Stand-Up India", "state": "ALL", "category": "business",
      "source_url": "https://www.myscheme.gov.in/schemes/sui",
      "criteria": {"states": ["ALL"], "age": {"min": 18}}},
     {"details": "Stand-Up India facilitates bank loans between Rs 10 lakh and Rs 1 crore for greenfield enterprises.",
      "eligibility": "SC/ST and women entrepreneurs above 18 years of age.",
      "application_process": "Apply through the Stand-Up Mitra portal or approach your nearest bank branch."}),
]


def build_store() -> MemoryStore:
    store = MemoryStore()
    for scheme, sections in SCHEMES:
        index_scheme(dict(scheme), {"en": sections}, store=store)
    return store


@pytest.fixture()
def store():
    s = build_store()
    set_store(s)
    set_cache(MemoryCache())
    Agent._indexes.clear()
    yield s
    set_store(None)
    set_cache(None)
    Agent._indexes.clear()
