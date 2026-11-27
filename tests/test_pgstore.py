"""Postgres integration tests. Skipped unless TEST_DATABASE_URL points at a pgvector database:

    docker run -d -p 55432:5432 -e POSTGRES_USER=haqdar -e POSTGRES_PASSWORD=haqdar -e POSTGRES_DB=haqdar \
        pgvector/pgvector:pg16
    TEST_DATABASE_URL=postgresql://haqdar:haqdar@localhost:55432/haqdar pytest tests/test_pgstore.py
"""
import os
import uuid

import pytest

from backend.agent.flow import Agent, AgentConfig
from backend.core.cache import MemoryCache
from backend.rag.indexer import content_hash, index_scheme
from backend.rag.search import retrieve
from backend.rag.store import Filters, PgStore
from tests.conftest import SCHEMES

DSN = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


@pytest.fixture(scope="module")
def pg():
    import psycopg
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS eval_questions, change_log, sessions, chunks, scheme_versions, schemes CASCADE")
    PgStore.init_schema(DSN)
    PgStore.init_schema(DSN)  # idempotent
    store = PgStore(DSN)
    for scheme, sections in SCHEMES:
        s = dict(scheme, content_hash=content_hash({"en": sections}))
        assert index_scheme(s, {"en": sections}, store=store) == "added"
    yield store
    store.pool.close()


def test_upsert_unchanged_and_updated(pg):
    scheme, sections = SCHEMES[0]
    s = dict(scheme, content_hash=content_hash({"en": sections}))
    assert index_scheme(s, {"en": sections}, store=pg) == "unchanged"
    changed = {"en": dict(sections, benefits=sections["benefits"] + " Updated in 2026.")}
    assert index_scheme(dict(scheme, content_hash=content_hash(changed)), changed, store=pg) == "updated"
    with pg.pool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM scheme_versions").fetchone()["n"] == 1


def test_vector_keyword_and_rerank_search(pg):
    for mode in ("vector", "hybrid", "rerank"):
        res = retrieve("Ujjwala LPG connection for women", mode=mode, store=pg)
        assert res.hits[0].slug == "pmuy", mode
    kw = pg.keyword_search("widow pension", 5, Filters())
    assert kw and kw[0].slug == "ignwps"
    res = retrieve("girl child Rs 25000", mode="hybrid", store=pg, filters=Filters(state="Bihar"))
    assert all(h.slug != "ksy" for h in res.hits)


def test_sessions_changelog_inactive(pg):
    sid = str(uuid.uuid4())
    pg.save_session(sid, {"age": 30}, [{"role": "user", "text": "hi"}])
    pg.save_session(sid, {"age": 31}, [])
    assert pg.get_session(sid)["profile"] == {"age": 31}
    pg.add_change_log(1, 2, 0, {"note": "test"})
    assert pg.recent_updates(1)[0]["updated"] == 2
    assert pg.mark_inactive(["sui"]) == 1
    assert all(h.slug != "sui" for h in retrieve("Stand-Up India loan", mode="hybrid", store=pg).hits)


def test_agent_on_postgres(pg):
    Agent._indexes.clear()
    out = Agent(AgentConfig(), store=pg, cache=MemoryCache()).run("PM Kisan mein kitne paise milte hain?")
    assert out["answer"]["citations"][0]["slug"] == "pm-kisan"
    assert pg.get_session(out["session_id"]) is not None
