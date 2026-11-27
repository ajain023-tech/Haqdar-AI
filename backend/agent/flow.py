"""The agent: a plain-Python state machine wiring profile -> router -> rules -> retrieval -> answer -> self-check.

    LOAD_SESSION -> DETECT_LANG -> EXTRACT_PROFILE -> ROUTE -> CACHE?
        find_schemes:    RULES -> FOLLOW_UP? -> RETRIEVE (filtered to eligible/maybe schemes)
        scheme_question: RESOLVE -> RETRIEVE (filtered to the named scheme, if resolved)
    -> GENERATE (streamed) -> SELF_CHECK -> [RE-RETRIEVE + REGENERATE once] -> CARDS -> SAVE -> DONE

`Agent.events()` yields the stream the /chat endpoint sends as SSE:
    {"type": "meta", ...}      session id, language, intent, profile
    {"type": "token", "text"}  answer pieces as the LLM writes them
    {"type": "revise", "text"} the self-check changed the answer: replace what was streamed
    {"type": "final", ...}     full response (answer, citations, scheme cards, follow-up, timings)
`Agent.run()` returns just the final payload (eval, non-streaming clients).

Setup flags map onto the eval rows: mode = vector | hybrid | rerank, use_rules, use_selfcheck.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional

from backend.agent.router import NAME_GENERIC, Route, SchemeIndex, named_phrase, route
from backend.core.cache import Cache, cache_key, get_cache
from backend.core.config import Settings, get_settings
from backend.core.lang import detect_lang
from backend.core.tracing import Trace
from backend.core.usage import record_query
from backend.providers.embeddings import Embedder, get_embedder
from backend.providers.llm import LLM, get_llm
from backend.providers.reranker import Reranker, get_reranker
from backend.rag import answer as answer_mod
from backend.rag import selfcheck
from backend.rag.query import rewrite_query
from backend.rag.search import RetrievalResult, retrieve
from backend.rag.store import Filters, Store, get_store, tokenize
from backend.rules import evaluate, extract_profile, pick_followup
from backend.rules.engine import Verdict

MAX_CARDS = 6
NAMED_MATCH_SHARE = 0.6      # share of a named scheme's distinctive words a hit's name must contain


@dataclass
class AgentConfig:
    mode: str = "rerank"
    use_rules: bool = True
    use_selfcheck: bool = True
    use_cache: bool = True
    top_k: int = 5


class Agent:
    _indexes: Dict[int, SchemeIndex] = {}

    def __init__(self, config: Optional[AgentConfig] = None, store: Optional[Store] = None,
                 llm: Optional[LLM] = None, embedder: Optional[Embedder] = None,
                 reranker: Optional[Reranker] = None, cache: Optional[Cache] = None,
                 settings: Optional[Settings] = None) -> None:
        self.cfg = config or AgentConfig()
        self.store = store or get_store()
        self.llm = llm or get_llm()
        self.embedder = embedder or get_embedder()
        self.reranker = reranker or get_reranker()
        self.cache = cache or get_cache()
        self.settings = settings or get_settings()
        key = id(self.store)
        if key not in Agent._indexes:
            Agent._indexes[key] = SchemeIndex(self.store)
        self.index = Agent._indexes[key]
        self.last_hits: list = []

    # ------------------------------------------------------------------ public API
    def run(self, message: str, session_id: Optional[str] = None, lang: Optional[str] = None,
            profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        final: Dict[str, Any] = {}
        for ev in self.events(message, session_id, lang, profile):
            if ev["type"] == "final":
                final = ev
        return final

    def events(self, message: str, session_id: Optional[str] = None, lang: Optional[str] = None,
               profile: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
        t_start = time.perf_counter()
        record_query()
        session_id = session_id or str(uuid.uuid4())
        trace = Trace("chat", input={"message": message}, session_id=session_id,
                      metadata={"mode": self.cfg.mode, "rules": self.cfg.use_rules,
                                "selfcheck": self.cfg.use_selfcheck})

        # LOAD_SESSION
        with trace.span("load_session"):
            sess = self.store.get_session(session_id) or {}
            prev_profile = sess.get("profile") or {}
            history: List[Dict[str, Any]] = sess.get("history") or []
            pending = prev_profile.pop("_pending_followup", None)
            asked = prev_profile.pop("_asked", [])
            prev_lang = prev_profile.pop("_lang", None)
        # A short reply to our follow-up ("haan", "45", "नहीं") continues the previous question:
        # keep its language and search with it, while the reply only fills in the profile.
        reply_to_followup = bool(pending) and len(message.split()) <= 4
        last_question = next((h["text"] for h in reversed(history) if h.get("role") == "user"), None)
        ask = f"{last_question} ({message})" if reply_to_followup and last_question else message

        # DETECT_LANG + EXTRACT_PROFILE
        lang = lang if lang in ("en", "hi", "hinglish") else (
            prev_lang if reply_to_followup and prev_lang else detect_lang(message))
        with trace.span("extract_profile", input={"lang": lang}) as sp:
            prof = extract_profile(message, prev_profile, llm=self.llm, pending=pending)
            if profile:
                prof.update({k: v for k, v in profile.items() if v is not None})
            sp["output"] = prof

        # ROUTE
        with trace.span("route") as sp:
            r = route(ask, self.index, has_pending_followup=bool(pending), llm=self.llm)
            sp["output"] = {"intent": r.intent, "scheme_ids": r.scheme_ids, "named": r.named}

        yield {"type": "meta", "session_id": session_id, "lang": lang, "intent": r.intent, "profile": prof,
               "trace_id": trace.id}

        # CACHE
        ckey = cache_key(ask, lang, f"{self.cfg.mode}:{self.cfg.use_rules}:{self.cfg.use_selfcheck}", prof)
        if self.cfg.use_cache:
            cached = self.cache.get(ckey)
            if cached:
                cached = dict(cached, session_id=session_id, cached=True, trace_id=trace.id)
                yield {"type": "token", "text": cached["answer"]["text"]}
                self._save(session_id, prof, history, ask, cached, asked, lang)
                trace.end({"cached": True})
                yield {"type": "final", **cached}
                return

        # RULES (find_schemes) or RESOLVE (scheme_question)
        verdicts: List[Verdict] = []
        follow_up = None
        filters = Filters(state=prof.get("state"))
        query = ask
        if r.intent == "find_schemes" and self.cfg.use_rules:
            with trace.span("rules") as sp:
                verdicts = evaluate(prof, self.store.list_schemes(state=prof.get("state")), lang)
                follow_up = pick_followup(verdicts, lang, asked)
                # Rules only *exclude*: retrieval searches every scheme not ruled out, and the
                # eligible/maybe ordering decides the cards. (Cutting to a top-N here drops the right
                # scheme whenever the profile is still thin.)
                if any(v.verdict == "no" for v in verdicts):
                    filters.scheme_ids = [v.scheme_id for v in verdicts if v.verdict != "no"]
                sp["output"] = {"eligible": sum(v.verdict == "eligible" for v in verdicts),
                                "maybe": sum(v.verdict == "maybe" for v in verdicts),
                                "no": sum(v.verdict == "no" for v in verdicts), "follow_up": follow_up}
        elif r.scheme_ids:
            filters.scheme_ids = r.scheme_ids
            filters.state = None  # the user named it: answer even if it's another state's scheme

        # REWRITE (hi / hinglish -> English search query) + RETRIEVE
        with trace.span("rewrite_query") as sp:
            alt = rewrite_query(ask, lang, llm=self.llm)
            sp["output"] = alt
        with trace.span("retrieve", input={"query": query, "alt_query": alt, "mode": self.cfg.mode}) as sp:
            res = self._retrieve(query, filters, alt)
            sp["output"] = {"top_score": res.top_score, "reranker": res.reranker,
                            "hits": [(h.slug, h.section, round(h.score, 3)) for h in res.hits]}

        refuse_named = r.named is not None and not r.named_resolved and not self._named_matches(r, res, alt)
        # The "I don't know" threshold guards open questions. When the scheme was resolved by name, or the
        # rule engine shortlisted it, we already know it exists in official data, so don't refuse on score.
        known_scheme = bool(r.scheme_ids)
        threshold = self.settings.idk_threshold if (self.cfg.mode == "rerank" and not known_scheme) else 0.0

        # GENERATE (streamed)
        verdict_dicts = [v.to_dict() for v in verdicts if v.verdict != "no"][:8]
        with trace.span("generate") as sp:
            if refuse_named:
                pieces = [answer_mod.REFUSAL.get(lang, answer_mod.REFUSAL["en"])]
                yield {"type": "token", "text": pieces[0]}
            else:
                pieces = []
                for piece in answer_mod.stream_generate(ask, res.hits, lang, res.top_score, threshold,
                                                        verdict_dicts, prof, llm=self.llm):
                    pieces.append(piece)
                    yield {"type": "token", "text": piece}
            ans = answer_mod.finalize("".join(pieces), res.hits, lang)
            sp["output"] = {"refused": ans.refused, "chars": len(ans.text)}

        # SELF_CHECK (+ one re-retrieval)
        check = None
        if self.cfg.use_selfcheck and not ans.refused:
            with trace.span("self_check") as sp:
                streamed = ans.text
                check = selfcheck.check(ans.text, res.hits, llm=self.llm)
                if not check.ok:
                    extra = " ".join(c.claim for c in check.unsupported)[:300]
                    res2 = self._retrieve(f"{query} {extra}", filters, alt)
                    ans2 = answer_mod.generate(ask, res2.hits, lang, res2.top_score, threshold,
                                               verdict_dicts, prof, llm=self.llm)
                    check2 = selfcheck.check(ans2.text, res2.hits, llm=self.llm)
                    if check2.faithfulness >= check.faithfulness:
                        ans, check, res = ans2, check2, res2
                    if not check.ok:
                        cleaned = selfcheck.strip_unsupported(ans.text, check)
                        if len(selfcheck.split_claims(cleaned)) == 0:
                            ans = answer_mod.Answer(answer_mod.REFUSAL.get(lang, answer_mod.REFUSAL["en"]),
                                                    lang, [], refused=True)
                        else:
                            ans = answer_mod.finalize(cleaned, res.hits, lang)
                if ans.text != streamed:
                    yield {"type": "revise", "text": ans.text}
                sp["output"] = {"ok": check.ok, "faithfulness": check.faithfulness}

        # CARDS
        self.last_hits = res.hits  # chunks the final answer was generated from (eval judges against these)
        cards = self._cards(verdicts, ans, prof, res.hits)
        if follow_up and cards:
            # Ask about what matters for the schemes on screen first; fall back to the whole list.
            shown = {c["slug"] for c in cards}
            follow_up = pick_followup([v for v in verdicts if v.slug in shown], lang, asked) or follow_up
        timings = dict(trace.timings_ms)
        timings.update({f"retrieve_{k}": round(v, 1) for k, v in res.timings_ms.items()})
        timings["total"] = round((time.perf_counter() - t_start) * 1000, 1)

        payload = {
            "session_id": session_id, "lang": lang, "intent": r.intent, "profile": prof,
            "answer": ans.to_dict(), "schemes": cards, "follow_up": follow_up,
            "self_check": check.to_dict() if check else None,
            "retrieval": {"mode": res.mode, "top_score": round(res.top_score, 4), "reranker": res.reranker,
                          "hits": [{"slug": h.slug, "section": h.section, "score": round(h.score, 4),
                                    "chunk_id": h.chunk_id} for h in res.hits]},
            "timings_ms": timings, "cached": False, "trace_id": trace.id,
        }
        if self.cfg.use_cache:
            self.cache.set(ckey, {k: v for k, v in payload.items() if k not in ("session_id", "trace_id")},
                           ttl=self.settings.cache_ttl_seconds)
        self._save(session_id, prof, history, ask, payload, asked, lang)
        trace.end({"answer": ans.text[:500], "refused": ans.refused})
        yield {"type": "final", **payload}

    # ------------------------------------------------------------------ helpers
    def _retrieve(self, query: str, filters: Filters, alt_query: Optional[str] = None) -> RetrievalResult:
        return retrieve(query, mode=self.cfg.mode, filters=filters, top_k=self.cfg.top_k, store=self.store,
                        embedder=self.embedder, reranker=self.reranker, alt_query=alt_query)

    def _named_matches(self, r: Route, res: RetrievalResult, alt: Optional[str] = None) -> bool:
        """A named-but-unresolved scheme counts as found only if one top hit's name contains most of its
        distinctive words ("Digital Gaay Bima" vs "Suraksha Bima" -> 1/3 -> not found)."""
        phrases = [r.named or ""]
        alt_named = named_phrase(alt) if alt else None
        if alt_named:
            phrases.append(alt_named)  # English transliteration of a Devanagari name (LLM rewrite)
        for phrase in phrases:
            wanted = set(tokenize(phrase)) - NAME_GENERIC
            if not wanted:
                return True
            for h in res.hits[:3]:
                if len(wanted & set(tokenize(h.name))) / len(wanted) >= NAMED_MATCH_SHARE:
                    return True
        return False

    def _cards(self, verdicts: List[Verdict], ans: answer_mod.Answer, prof: Dict[str, Any],
               hits: Optional[list] = None) -> List[Dict[str, Any]]:
        """Cards in the order that matters to this question: cited schemes, then other retrieved ones,
        then remaining eligible/maybe schemes from the rule engine. Ruled-out schemes and duplicate
        names (the same scheme listed under several slugs) are skipped."""
        if ans.refused and not verdicts:
            return []
        by_verdict = {v.scheme_id: v for v in verdicts}
        order: List[int] = []
        cited_slugs = [c["slug"] if isinstance(c, dict) else c.slug for c in ans.citations]
        for slug in cited_slugs:
            s = self.store.get_scheme(slug)
            if s:
                order.append(s["id"])
        order += [h.scheme_id for h in (hits or [])]
        order += [v.scheme_id for v in verdicts if v.verdict == "eligible"]
        order += [v.scheme_id for v in verdicts if v.verdict == "maybe" and v.missing]
        seen_ids, seen_names, picked = set(), set(), []
        schemes = {s["id"]: s for s in self.store.get_schemes(list(dict.fromkeys(order)))}
        for sid in order:
            s = schemes.get(sid)
            v = by_verdict.get(sid)
            if not s or sid in seen_ids or (v and v.verdict == "no"):
                continue
            key = " ".join(s["name"].lower().split())
            if key in seen_names:
                continue
            seen_ids.add(sid)
            seen_names.add(key)
            picked.append((sid, v))
            if len(picked) >= MAX_CARDS:
                break
        cards = []
        for sid, v in picked:
            s = schemes[sid]
            if v is None and prof:
                v = evaluate(prof, [s], ans.lang)[0]
            cards.append({
                "slug": s["slug"], "name": s["name"], "name_hi": s.get("name_hi"), "state": s.get("state"),
                "category": s.get("category"),
                "verdict": v.verdict if v else None, "reasons": v.reasons if v else [],
                "missing": v.missing if v else [], "also_check": v.also_check if v else [],
                "benefits": (s.get("benefits") or "")[:400], "documents_required": s.get("documents_required") or [],
                "source_url": s.get("source_url"), "last_checked": s.get("last_checked"),
                "verification": s.get("verification"),
            })
        return cards

    def _save(self, session_id: str, prof: Dict[str, Any], history: List[Dict[str, Any]], message: str,
              payload: Dict[str, Any], asked: List[str], lang: str = "en") -> None:
        stored = dict(prof)
        stored["_lang"] = lang
        fu = payload.get("follow_up")
        if fu:
            stored["_pending_followup"] = fu["field"]
            stored["_asked"] = sorted(set(asked) | {fu["field"]})
        elif asked:
            stored["_asked"] = asked
        history = (history + [{"role": "user", "text": message},
                              {"role": "assistant", "text": payload["answer"]["text"][:1000]}])[-20:]
        try:
            self.store.save_session(session_id, stored, history)
        except Exception:  # noqa: BLE001 - a session write must not lose the answer
            pass
