"""Storage layer: one interface, two implementations.

* `PgStore`     — Postgres + pgvector (HNSW) + full-text (GIN on tsv). Production.
* `MemoryStore` — pure Python (cosine + BM25). Tests, CI eval and local runs without a database.

Everything above this layer (search, agent, API) talks only to `Store`.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from backend.rag.chunking import Chunk
from backend.providers.embeddings import cosine

_TOKEN = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)

SCHEME_FIELDS = ["slug", "name", "name_hi", "ministry", "state", "category", "benefits", "benefits_hi",
                 "documents_required", "criteria", "verification", "status", "source_url", "pdf_url",
                 "content_hash"]


def tokenize(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN.findall(text or "") if len(t) > 1]


@dataclass
class Filters:
    state: Optional[str] = None          # user's state; central ('ALL') schemes always match
    category: Optional[str] = None
    scheme_ids: Optional[Sequence[int]] = None
    lang: Optional[str] = None           # restrict to en / hi chunks


@dataclass
class Hit:
    chunk_id: int
    scheme_id: int
    slug: str
    name: str
    section: str
    lang: str
    text: str
    source_url: Optional[str] = None
    last_checked: Optional[str] = None
    verification: str = "auto"
    score: float = 0.0
    scores: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    # --- schemes ---
    def upsert_scheme(self, scheme: Dict[str, Any], raw_text: str = "") -> Tuple[int, str]:
        """Insert or update by slug. Returns (id, 'added' | 'updated' | 'unchanged')."""
        raise NotImplementedError

    def get_scheme(self, slug: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def get_schemes(self, ids: Iterable[int]) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def list_schemes(self, state: Optional[str] = None, status: str = "active") -> List[Dict[str, Any]]:
        raise NotImplementedError

    def mark_inactive(self, slugs: Iterable[str]) -> int:
        raise NotImplementedError

    def set_verification(self, slug: str, value: str) -> bool:
        """verified | auto | stale. Returns False if the slug is unknown."""
        raise NotImplementedError

    def set_criteria(self, slug: str, criteria: Dict[str, Any]) -> bool:
        """Replace criteria without touching content_hash (hand corrections, conflict flags)."""
        raise NotImplementedError

    # --- chunks ---
    def replace_chunks(self, scheme_id: int, chunks: List[Chunk], embeddings: List[List[float]]) -> int:
        raise NotImplementedError

    def vector_search(self, qvec: List[float], k: int, filters: Filters) -> List[Hit]:
        raise NotImplementedError

    def keyword_search(self, query: str, k: int, filters: Filters) -> List[Hit]:
        raise NotImplementedError

    # --- sessions, change log ---
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def save_session(self, session_id: str, profile: Dict[str, Any], history: List[Dict[str, Any]]) -> None:
        raise NotImplementedError

    def add_change_log(self, added: int, updated: int, inactive: int, details: Dict[str, Any]) -> None:
        raise NotImplementedError

    def recent_updates(self, limit: int = 20) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def ping(self) -> bool:
        return True


# ---------------------------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------------------------

class MemoryStore(Store):
    BM25_K1 = 1.5
    BM25_B = 0.75

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.schemes: Dict[int, Dict[str, Any]] = {}
        self.by_slug: Dict[str, int] = {}
        self.chunks: Dict[int, Dict[str, Any]] = {}
        self.versions: List[Dict[str, Any]] = []
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.change_log: List[Dict[str, Any]] = []
        self._next_scheme = 1
        self._next_chunk = 1
        self._df: Counter = Counter()
        self._avgdl = 1.0

    # schemes
    def upsert_scheme(self, scheme, raw_text=""):
        with self._lock:
            slug = scheme["slug"]
            row = {f: scheme.get(f) for f in SCHEME_FIELDS}
            row.setdefault("state", "ALL")
            row["state"] = row["state"] or "ALL"
            row["documents_required"] = row["documents_required"] or []
            row["criteria"] = row["criteria"] or {}
            row["verification"] = row["verification"] or "auto"
            row["status"] = row["status"] or "active"
            row["last_checked"] = now_iso()
            if slug in self.by_slug:
                sid = self.by_slug[slug]
                old = self.schemes[sid]
                if old.get("content_hash") and old.get("content_hash") == row.get("content_hash"):
                    old["last_checked"] = row["last_checked"]
                    old["status"] = "active"
                    return sid, "unchanged"
                self.versions.append({"scheme_id": sid, "content_hash": old.get("content_hash"),
                                      "criteria": old.get("criteria"), "captured_at": now_iso()})
                row.update(id=sid, updated_at=now_iso())
                self.schemes[sid] = row
                return sid, "updated"
            sid = self._next_scheme
            self._next_scheme += 1
            row.update(id=sid, updated_at=now_iso())
            self.schemes[sid] = row
            self.by_slug[slug] = sid
            return sid, "added"

    def get_scheme(self, slug):
        sid = self.by_slug.get(slug)
        return dict(self.schemes[sid]) if sid else None

    def get_schemes(self, ids):
        return [dict(self.schemes[i]) for i in ids if i in self.schemes]

    def list_schemes(self, state=None, status="active"):
        out = []
        for s in self.schemes.values():
            if status and s["status"] != status:
                continue
            if state and s["state"] not in ("ALL", state):
                continue
            out.append(dict(s))
        return out

    def mark_inactive(self, slugs):
        n = 0
        for slug in slugs:
            sid = self.by_slug.get(slug)
            if sid and self.schemes[sid]["status"] != "inactive":
                self.schemes[sid]["status"] = "inactive"
                n += 1
        return n

    def set_verification(self, slug, value):
        sid = self.by_slug.get(slug)
        if not sid:
            return False
        self.schemes[sid]["verification"] = value
        return True

    def set_criteria(self, slug, criteria):
        sid = self.by_slug.get(slug)
        if not sid:
            return False
        self.schemes[sid]["criteria"] = dict(criteria)
        return True

    # chunks
    def replace_chunks(self, scheme_id, chunks, embeddings):
        with self._lock:
            for cid in [c for c, v in self.chunks.items() if v["scheme_id"] == scheme_id]:
                del self.chunks[cid]
            for ch, emb in zip(chunks, embeddings):
                toks = tokenize(ch.text)
                self.chunks[self._next_chunk] = {
                    "id": self._next_chunk, "scheme_id": scheme_id, "lang": ch.lang, "section": ch.section,
                    "page": ch.page, "text": ch.text, "embedding": emb, "tf": Counter(toks), "len": len(toks),
                }
                self._next_chunk += 1
            self._reindex_stats()
            return len(chunks)

    def _reindex_stats(self) -> None:
        df: Counter = Counter()
        total = 0
        for c in self.chunks.values():
            df.update(set(c["tf"]))
            total += c["len"]
        self._df = df
        self._avgdl = (total / len(self.chunks)) if self.chunks else 1.0

    def _passes(self, c: Dict[str, Any], f: Filters) -> bool:
        s = self.schemes.get(c["scheme_id"])
        if not s or s["status"] != "active":
            return False
        if f.state and s["state"] not in ("ALL", f.state):
            return False
        if f.category and (s.get("category") or "").lower() != f.category.lower():
            return False
        if f.scheme_ids is not None and c["scheme_id"] not in f.scheme_ids:
            return False
        if f.lang and c["lang"] != f.lang:
            return False
        return True

    def _hit(self, c: Dict[str, Any], score: float) -> Hit:
        s = self.schemes[c["scheme_id"]]
        return Hit(chunk_id=c["id"], scheme_id=c["scheme_id"], slug=s["slug"], name=s["name"],
                   section=c["section"], lang=c["lang"], text=c["text"], source_url=s.get("source_url"),
                   last_checked=s.get("last_checked"), verification=s.get("verification", "auto"), score=score)

    def vector_search(self, qvec, k, filters):
        scored = [(cosine(qvec, c["embedding"]), c) for c in self.chunks.values()
                  if c["embedding"] is not None and self._passes(c, filters)]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [self._hit(c, s) for s, c in scored[:k]]

    def keyword_search(self, query, k, filters):
        q = set(tokenize(query))
        if not q:
            return []
        n = len(self.chunks) or 1
        scored = []
        for c in self.chunks.values():
            if not self._passes(c, filters):
                continue
            score = 0.0
            for t in q:
                tf = c["tf"].get(t, 0)
                if not tf:
                    continue
                df = self._df.get(t, 0)
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                denom = tf + self.BM25_K1 * (1 - self.BM25_B + self.BM25_B * c["len"] / self._avgdl)
                score += idf * tf * (self.BM25_K1 + 1) / denom
            if score > 0:
                scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [self._hit(c, s) for s, c in scored[:k]]

    # sessions / change log
    def get_session(self, session_id):
        s = self.sessions.get(session_id)
        return json.loads(json.dumps(s)) if s else None

    def save_session(self, session_id, profile, history):
        self.sessions[session_id] = {"id": session_id, "profile": profile, "history": history,
                                     "created_at": self.sessions.get(session_id, {}).get("created_at", now_iso())}

    def add_change_log(self, added, updated, inactive, details):
        self.change_log.append({"id": len(self.change_log) + 1, "run_at": now_iso(), "added": added,
                                "updated": updated, "inactive": inactive, "details": details})

    def recent_updates(self, limit=20):
        return list(reversed(self.change_log))[:limit]


# ---------------------------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------------------------

class PgStore(Store):
    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 5) -> None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        from pgvector.psycopg import register_vector

        def _configure(conn):
            register_vector(conn)

        self.pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, kwargs={"row_factory": dict_row},
                                   configure=_configure, open=True)

    @staticmethod
    def init_schema(dsn: str, path: str = "db/schema.sql") -> None:
        import psycopg
        with psycopg.connect(dsn, autocommit=True) as conn, open(path, encoding="utf-8") as fh:
            conn.execute(fh.read())

    def ping(self) -> bool:
        try:
            with self.pool.connection() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _row(r: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(r)
        for k in ("last_checked", "updated_at", "run_at", "created_at", "captured_at"):
            if isinstance(out.get(k), datetime):
                out[k] = out[k].isoformat()
        return out

    def upsert_scheme(self, scheme, raw_text=""):
        from psycopg.types.json import Jsonb
        vals = {f: scheme.get(f) for f in SCHEME_FIELDS}
        vals["state"] = vals["state"] or "ALL"
        vals["documents_required"] = vals["documents_required"] or []
        vals["criteria"] = Jsonb(vals["criteria"] or {})
        vals["verification"] = vals["verification"] or "auto"
        vals["status"] = vals["status"] or "active"
        with self.pool.connection() as conn:
            old = conn.execute("SELECT id, content_hash, criteria FROM schemes WHERE slug = %s",
                               (vals["slug"],)).fetchone()
            if old and old["content_hash"] and old["content_hash"] == vals["content_hash"]:
                conn.execute("UPDATE schemes SET last_checked = now(), status = 'active' WHERE id = %s",
                             (old["id"],))
                return old["id"], "unchanged"
            if old:
                conn.execute(
                    "INSERT INTO scheme_versions (scheme_id, content_hash, raw_text, criteria) "
                    "SELECT id, content_hash, %s, criteria FROM schemes WHERE id = %s", (raw_text, old["id"]))
            cols = ", ".join(SCHEME_FIELDS)
            ph = ", ".join(f"%({f})s" for f in SCHEME_FIELDS)
            upd = ", ".join(f"{f} = EXCLUDED.{f}" for f in SCHEME_FIELDS if f != "slug")
            row = conn.execute(
                f"INSERT INTO schemes ({cols}) VALUES ({ph}) ON CONFLICT (slug) DO UPDATE SET {upd}, "
                f"last_checked = now(), updated_at = now() RETURNING id", vals).fetchone()
            return row["id"], "updated" if old else "added"

    def get_scheme(self, slug):
        with self.pool.connection() as conn:
            r = conn.execute("SELECT * FROM schemes WHERE slug = %s", (slug,)).fetchone()
        return self._row(r) if r else None

    def get_schemes(self, ids):
        ids = list(ids)
        if not ids:
            return []
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT * FROM schemes WHERE id = ANY(%s)", (ids,)).fetchall()
        by_id = {r["id"]: self._row(r) for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    def list_schemes(self, state=None, status="active"):
        sql, args = "SELECT * FROM schemes WHERE status = %s", [status]
        if state:
            sql += " AND state IN ('ALL', %s)"
            args.append(state)
        with self.pool.connection() as conn:
            return [self._row(r) for r in conn.execute(sql, args).fetchall()]

    def mark_inactive(self, slugs):
        slugs = list(slugs)
        if not slugs:
            return 0
        with self.pool.connection() as conn:
            cur = conn.execute("UPDATE schemes SET status = 'inactive', updated_at = now() "
                               "WHERE slug = ANY(%s) AND status <> 'inactive'", (slugs,))
            return cur.rowcount

    def set_verification(self, slug, value):
        with self.pool.connection() as conn:
            cur = conn.execute("UPDATE schemes SET verification = %s WHERE slug = %s", (value, slug))
            return cur.rowcount > 0

    def set_criteria(self, slug, criteria):
        from psycopg.types.json import Jsonb
        with self.pool.connection() as conn:
            cur = conn.execute("UPDATE schemes SET criteria = %s, updated_at = now() WHERE slug = %s",
                               (Jsonb(criteria), slug))
            return cur.rowcount > 0

    def replace_chunks(self, scheme_id, chunks, embeddings):
        import numpy as np
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM chunks WHERE scheme_id = %s", (scheme_id,))
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO chunks (scheme_id, lang, section, page, text, embedding) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        [(scheme_id, c.lang, c.section, c.page, c.text, np.array(e, dtype=np.float32))
                         for c, e in zip(chunks, embeddings)])
        return len(chunks)

    @staticmethod
    def _where(f: Filters, args: List[Any]) -> str:
        clauses = ["s.status = 'active'"]
        if f.state:
            clauses.append("s.state IN ('ALL', %s)")
            args.append(f.state)
        if f.category:
            clauses.append("lower(s.category) = lower(%s)")
            args.append(f.category)
        if f.scheme_ids is not None:
            clauses.append("c.scheme_id = ANY(%s)")
            args.append(list(f.scheme_ids))
        if f.lang:
            clauses.append("c.lang = %s")
            args.append(f.lang)
        return " AND ".join(clauses)

    _SELECT = ("SELECT c.id AS chunk_id, c.scheme_id, s.slug, s.name, c.section, c.lang, c.text, "
               "s.source_url, s.last_checked, s.verification")

    def _hits(self, rows) -> List[Hit]:
        out = []
        for r in rows:
            r = self._row(r)
            out.append(Hit(chunk_id=r["chunk_id"], scheme_id=r["scheme_id"], slug=r["slug"], name=r["name"],
                           section=r["section"], lang=r["lang"], text=r["text"], source_url=r["source_url"],
                           last_checked=r["last_checked"], verification=r["verification"],
                           score=float(r["score"])))
        return out

    def vector_search(self, qvec, k, filters):
        import numpy as np
        q = np.array(qvec, dtype=np.float32)
        args: List[Any] = [q]
        where = self._where(filters, args)
        args += [q, k]
        sql = (f"{self._SELECT}, 1 - (c.embedding <=> %s) AS score FROM chunks c JOIN schemes s "
               f"ON s.id = c.scheme_id WHERE {where} ORDER BY c.embedding <=> %s LIMIT %s")
        with self.pool.connection() as conn:
            # Pre-filtered HNSW: let the index scan further so filters don't starve the result.
            conn.execute("SET hnsw.ef_search = 100")
            return self._hits(conn.execute(sql, args).fetchall())

    def keyword_search(self, query, k, filters):
        terms = sorted(set(tokenize(query)))
        if not terms:
            return []
        # OR of terms: recall-oriented BM25-style ranking with ts_rank_cd (length-normalised).
        tsq = " | ".join(t.replace("'", "") for t in terms)
        args: List[Any] = [tsq]
        where = self._where(filters, args)
        args = [tsq] + args + [k]
        sql = (f"{self._SELECT}, ts_rank_cd(c.tsv, to_tsquery('simple', %s), 32) AS score FROM chunks c "
               f"JOIN schemes s ON s.id = c.scheme_id WHERE c.tsv @@ to_tsquery('simple', %s) AND {where} "
               f"ORDER BY score DESC LIMIT %s")
        with self.pool.connection() as conn:
            return self._hits(conn.execute(sql, args).fetchall())

    def get_session(self, session_id):
        with self.pool.connection() as conn:
            r = conn.execute("SELECT * FROM sessions WHERE id = %s", (session_id,)).fetchone()
        return self._row(r) if r else None

    def save_session(self, session_id, profile, history):
        from psycopg.types.json import Jsonb
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO sessions (id, profile, history) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET profile = EXCLUDED.profile, history = EXCLUDED.history",
                (uuid.UUID(session_id), Jsonb(profile), Jsonb(history[-20:])))

    def add_change_log(self, added, updated, inactive, details):
        from psycopg.types.json import Jsonb
        with self.pool.connection() as conn:
            conn.execute("INSERT INTO change_log (added, updated, inactive, details) VALUES (%s, %s, %s, %s)",
                         (added, updated, inactive, Jsonb(details)))

    def recent_updates(self, limit=20):
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT * FROM change_log ORDER BY run_at DESC LIMIT %s", (limit,)).fetchall()
        return [self._row(r) for r in rows]


_store: Optional[Store] = None


def get_store() -> Store:
    global _store
    if _store is None:
        from backend.core.config import get_settings
        dsn = get_settings().database_url
        # Serverless instances (Vercel) each hold their own pool: keep it small.
        size = int(os.environ.get("DB_POOL_MAX", "5"))
        _store = PgStore(dsn, max_size=size) if dsn else MemoryStore()
    return _store


def set_store(store: Optional[Store]) -> None:
    global _store
    _store = store
