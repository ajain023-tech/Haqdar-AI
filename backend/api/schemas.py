"""Request/response models for the frozen API contract."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

Mode = Literal["vector", "hybrid", "rerank"]


class Profile(BaseModel):
    """Eligibility fields only. Names, phone numbers and Aadhaar are never accepted."""
    state: Optional[str] = None
    age: Optional[int] = Field(None, ge=0, le=120)
    gender: Optional[Literal["male", "female", "other"]] = None
    occupation: Optional[str] = None
    annual_income: Optional[int] = Field(None, ge=0)
    land_acres: Optional[float] = Field(None, ge=0)
    social_category: Optional[Literal["SC", "ST", "OBC", "General"]] = None
    disability: Optional[bool] = None
    bpl: Optional[bool] = None

    model_config = {"extra": "ignore"}


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: Optional[str] = None
    lang: Optional[Literal["en", "hi", "hinglish", "auto"]] = "auto"
    profile: Optional[Profile] = None
    stream: bool = True

    @field_validator("session_id")
    @classmethod
    def _uuid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        import uuid
        try:
            return str(uuid.UUID(v))
        except ValueError as exc:
            raise ValueError("session_id must be a UUID") from exc


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    state: Optional[str] = None
    category: Optional[str] = None
    lang: Optional[Literal["en", "hi"]] = None
    mode: Mode = "rerank"
    k: int = Field(5, ge=1, le=20)


class EligibilityRequest(BaseModel):
    profile: Profile
    lang: Literal["en", "hi", "hinglish"] = "en"
    limit: int = Field(50, ge=1, le=500)


class EligibilityItem(BaseModel):
    slug: str
    name: str
    verdict: Literal["eligible", "maybe", "no"]
    reasons: List[str]
    missing: List[str]
    also_check: List[str] = []
    source_url: Optional[str] = None
    last_checked: Optional[str] = None
    verification: Optional[str] = None


class EligibilityResponse(BaseModel):
    profile: Dict[str, Any]
    counts: Dict[str, int]
    schemes: List[EligibilityItem]
    follow_up: Optional[Dict[str, Any]] = None


class IngestRequest(BaseModel):
    source: Literal["hf", "scraper"] = "hf"   # scraper = Person B's ingest.refresh.run()
    slugs: Optional[List[str]] = None
    state: str = "Uttar Pradesh"
    limit: int = 0
