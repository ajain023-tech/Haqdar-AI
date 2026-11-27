"""Runtime settings, read once from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _load_dotenv(path: str = ".env") -> None:
    """Tiny .env loader so local runs work without python-dotenv."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            os.environ.setdefault(k.strip(), v.split(" #", 1)[0].strip())


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    redis_url: str = ""

    gemini_api_key: str = ""
    llm_provider: str = "gemini"
    llm_model: str = "gemini-2.5-flash"
    embed_provider: str = "gemini"
    embed_model: str = "gemini-embedding-001"
    embed_dim: int = 768

    reranker_url: str = ""
    reranker_token: str = ""
    rerank_provider: str = "space"
    idk_threshold: float = 0.15

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    admin_token: str = ""
    cors_origins: List[str] = field(default_factory=list)
    rate_limit_per_min: int = 30
    cache_ttl_seconds: int = 86400

    @property
    def offline(self) -> bool:
        """True when no LLM key is set: fake providers are used (tests, CI, local dev)."""
        return not self.gemini_api_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv()
    key = _env("GEMINI_API_KEY")
    return Settings(
        database_url=_env("DATABASE_URL"),
        redis_url=_env("REDIS_URL"),
        gemini_api_key=key,
        llm_provider=_env("LLM_PROVIDER", "gemini" if key else "fake"),
        llm_model=_env("LLM_MODEL", "gemini-2.5-flash"),
        embed_provider=_env("EMBED_PROVIDER", "gemini" if key else "hash"),
        embed_model=_env("EMBED_MODEL", "gemini-embedding-001"),
        embed_dim=int(_env("EMBED_DIM", "768")),
        reranker_url=_env("RERANKER_URL"),
        reranker_token=_env("RERANKER_TOKEN"),
        rerank_provider=_env("RERANK_PROVIDER", "space"),
        idk_threshold=float(_env("IDK_THRESHOLD", "0.15")),
        langfuse_public_key=_env("LANGFUSE_PUBLIC_KEY"),
        langfuse_secret_key=_env("LANGFUSE_SECRET_KEY"),
        langfuse_host=_env("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        admin_token=_env("ADMIN_TOKEN"),
        cors_origins=[o.strip() for o in _env("CORS_ORIGINS", "*").split(",") if o.strip()],
        rate_limit_per_min=int(_env("RATE_LIMIT_PER_MIN", "30")),
        cache_ttl_seconds=int(_env("CACHE_TTL_SECONDS", "86400")),
    )
