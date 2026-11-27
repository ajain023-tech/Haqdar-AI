"""Rules lane — OWNED BY PERSON B (rule engine, profile extractor, follow-up picker).

Person A ships only the interface below plus a minimal working version so the agent flow
(profile -> rules -> retrieval -> answer) can be built and tested end to end. Person B replaces the
bodies of `extract_profile`, `evaluate` and `pick_followup`; the signatures are the contract.
"""
from backend.rules.engine import Verdict, evaluate  # noqa: F401
from backend.rules.followup import pick_followup  # noqa: F401
from backend.rules.profile import PROFILE_FIELDS, extract_profile  # noqa: F401
