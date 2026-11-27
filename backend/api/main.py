"""FastAPI app: the frozen API contract.

    POST /chat            streamed answer (SSE) or JSON, citations, scheme cards, follow-up question
    POST /voice           transcript + same as /chat + audio URL
    POST /eligibility     profile -> ranked schemes with eligible / maybe / no + reasons
    POST /search          retrieval only (debug, eval, Day 1 unblocker)
    GET  /schemes/{slug}  scheme detail, documents, source, last checked, verification
    GET  /updates         recent change log
    POST /admin/ingest    trigger re-crawl / re-load (X-Admin-Token)
    GET  /health          liveness, used to keep the free server warm
    GET  /metrics         usage counters for the free-tier watch

Run: uvicorn backend.api.main:app --reload
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, Iterator, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from backend.agent.flow import Agent, AgentConfig
from backend.api.schemas import ChatRequest, EligibilityRequest, EligibilityResponse, IngestRequest, SearchRequest
from backend.core import usage
from backend.core.config import get_settings
from backend.core.ratelimit import RateLimitMiddleware
from backend.providers.speech import get_speech
from backend.rag.search import retrieve
from backend.rag.store import Filters, get_store
from backend.rules import evaluate, pick_followup

VERSION = "0.1.0"
log = logging.getLogger("haqdar")
DATA_CREDIT = "Data: myScheme.gov.in, Govt. of India"

app = FastAPI(title="Haqdar AI", version=VERSION,
              description="Hakk se, apni yojana. Unofficial assistant; " + DATA_CREDIT)
settings = get_settings()
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins or ["*"], allow_methods=["*"],
                   allow_headers=["*"])

app.add_middleware(RateLimitMiddleware)

_ingest_lock = threading.Lock()
_ingest_state: Dict[str, Any] = {"running": False, "last": None}


def _sse(event: Dict[str, Any]) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def _chat_stream(events: Iterator[Dict[str, Any]]) -> Iterator[str]:
    try:
        for ev in events:
            yield _sse(ev)
    except Exception as exc:  # noqa: BLE001 - tell the client instead of cutting the stream
        log.exception("chat stream failed")
        yield _sse({"type": "error", "detail": "Something went wrong. Please try again.", "error": type(exc).__name__})


@app.get("/health")
def health() -> Dict[str, Any]:
    store = get_store()
    return {"status": "ok", "version": VERSION, "db": store.ping(), "store": type(store).__name__,
            "llm": settings.llm_provider if not settings.offline else "fake"}


@app.get("/metrics")
def metrics() -> Dict[str, Any]:
    return usage.snapshot()


@app.post("/chat")
def chat(req: ChatRequest):
    agent = Agent(AgentConfig())
    lang = None if req.lang in (None, "auto") else req.lang
    profile = req.profile.model_dump(exclude_none=True) if req.profile else None
    events = agent.events(req.message, req.session_id, lang, profile)
    if req.stream:
        return StreamingResponse(_chat_stream(events), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    final: Dict[str, Any] = {}
    for ev in events:
        if ev["type"] == "final":
            final = ev
    final.pop("type", None)
    return JSONResponse(final)


@app.post("/voice")
async def voice(audio: UploadFile = File(...), session_id: Optional[str] = Form(None),
                lang: Optional[str] = Form(None), transcript: Optional[str] = Form(None),
                speak: bool = Form(True)):
    """Upload audio -> transcript -> same answer as /chat -> optional TTS.

    The UI shows the transcript for editing first: send the edited text back as `transcript`
    (with any small audio blob) to skip re-transcribing.
    """
    speech = get_speech()
    text = (transcript or "").strip()
    detected = None
    if not text:
        if speech is None:
            raise HTTPException(503, "Voice is not configured on this server (set SARVAM_API_KEY).")
        raw = await audio.read()
        if len(raw) > 10 * 1024 * 1024:
            raise HTTPException(413, "Audio too large (max 10 MB).")
        text, detected = speech.transcribe(raw, audio.filename or "audio.webm",
                                           audio.content_type or "audio/webm")
    if not text:
        raise HTTPException(422, "Could not understand the audio. Please try again or type your question.")
    req = ChatRequest(message=text, session_id=session_id, lang=lang or "auto", stream=False)
    final = Agent(AgentConfig()).run(req.message, req.session_id, None if req.lang == "auto" else req.lang)
    final.pop("type", None)
    audio_url = None
    if speak and speech is not None:
        try:
            audio_url = speech.synthesize(final["answer"]["text"], final["lang"])
        except Exception:  # noqa: BLE001 - text answer still goes back
            log.exception("tts failed")
    return {"transcript": text, "detected_language": detected, **final, "audio_url": audio_url}


@app.post("/eligibility", response_model=EligibilityResponse)
def eligibility(req: EligibilityRequest):
    store = get_store()
    profile = req.profile.model_dump(exclude_none=True)
    schemes = store.list_schemes(state=profile.get("state"))
    verdicts = evaluate(profile, schemes, req.lang)
    by_id = {s["id"]: s for s in schemes}
    counts = {k: sum(v.verdict == k for v in verdicts) for k in ("eligible", "maybe", "no")}
    items = []
    for v in verdicts[:req.limit]:
        s = by_id[v.scheme_id]
        items.append({**v.to_dict(), "source_url": s.get("source_url"), "last_checked": s.get("last_checked"),
                      "verification": s.get("verification")})
    return {"profile": profile, "counts": counts, "schemes": items,
            "follow_up": pick_followup(verdicts, req.lang)}


@app.post("/search")
def search(req: SearchRequest):
    res = retrieve(req.query, mode=req.mode, filters=Filters(state=req.state, category=req.category, lang=req.lang),
                   top_k=req.k)
    return res.to_dict()


@app.get("/schemes/{slug}")
def scheme_detail(slug: str):
    s = get_store().get_scheme(slug)
    if not s:
        raise HTTPException(404, "Scheme not found")
    s.pop("content_hash", None)
    s["data_credit"] = DATA_CREDIT
    return s


@app.get("/updates")
def updates(limit: int = 20):
    return {"updates": get_store().recent_updates(min(max(limit, 1), 100))}


def _run_ingest(req: IngestRequest) -> None:
    try:
        if req.source == "hf":
            from scripts.load_hf_dataset import load
            result = load(state=req.state, limit=req.limit, slugs=req.slugs, log=log.info)
        else:
            from ingest.refresh import run  # Person B's daily refresh (sitemap diff + content_hash)
            result = run(slugs=req.slugs)
        _ingest_state["last"] = {"ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001
        log.exception("ingest failed")
        _ingest_state["last"] = {"ok": False, "error": str(exc)}
    finally:
        _ingest_state["running"] = False
        _ingest_lock.release()


@app.post("/admin/ingest", status_code=202)
def admin_ingest(req: IngestRequest, background: BackgroundTasks, x_admin_token: str = Header("")):
    if not settings.admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(401, "Invalid admin token")
    if req.source == "scraper":
        try:
            import ingest.refresh  # noqa: F401
        except ImportError:
            raise HTTPException(501, "Scraper refresh (ingest/refresh.py) is not in this build yet.")
    if not _ingest_lock.acquire(blocking=False):
        raise HTTPException(409, "An ingest run is already in progress")
    _ingest_state["running"] = True
    background.add_task(_run_ingest, req)
    return {"accepted": True, "source": req.source}


@app.get("/admin/ingest")
def admin_ingest_status(x_admin_token: str = Header("")):
    if not settings.admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(401, "Invalid admin token")
    return _ingest_state
