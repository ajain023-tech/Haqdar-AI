import json

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app


@pytest.fixture()
def client(store):
    return TestClient(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_chat_json(client):
    r = client.post("/chat", json={"message": "PM Kisan mein kitne paise milte hain?", "stream": False})
    body = r.json()
    assert r.status_code == 200
    assert body["answer"]["citations"] and body["session_id"]
    assert {"schemes", "follow_up", "timings_ms", "trace_id"} <= set(body)


def test_chat_stream_sse(client):
    with client.stream("POST", "/chat", json={"message": "Ujjwala ke liye documents?"}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        raw = "".join(r.iter_text())
    events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]
    assert events[0]["type"] == "meta" and events[-1]["type"] == "final"


def test_chat_rejects_bad_session_and_pii_fields(client):
    assert client.post("/chat", json={"message": "hi", "session_id": "not-a-uuid"}).status_code == 422
    r = client.post("/chat", json={"message": "PM Kisan?", "stream": False,
                                   "profile": {"age": 40, "aadhaar": "1234 5678 9012", "name": "X"}})
    assert r.status_code == 200
    assert "aadhaar" not in r.json()["profile"] and "name" not in r.json()["profile"]


def test_eligibility(client):
    r = client.post("/eligibility", json={"profile": {"state": "Uttar Pradesh", "gender": "female", "age": 45,
                                                      "bpl": True, "annual_income": 100000}})
    body = r.json()
    verdicts = {s["slug"]: s["verdict"] for s in body["schemes"]}
    assert verdicts["ignwps"] == "eligible" and verdicts["ksy"] == "eligible"
    assert body["counts"]["eligible"] >= 2


def test_scheme_detail_and_404(client):
    assert client.get("/schemes/pm-kisan").json()["name"].startswith("Pradhan Mantri")
    assert client.get("/schemes/does-not-exist").status_code == 404


def test_search_and_updates(client):
    r = client.post("/search", json={"query": "widow pension", "mode": "hybrid"})
    assert r.json()["hits"][0]["slug"] == "ignwps"
    assert "updates" in client.get("/updates").json()


def test_admin_requires_token(client):
    assert client.post("/admin/ingest", json={"source": "hf"}).status_code == 401


def test_admin_ingest_runs_scraper_refresh(client, monkeypatch):
    import ingest.refresh
    calls = []
    monkeypatch.setattr(ingest.refresh, "run", lambda slugs=None: calls.append(slugs) or {"added": 0})
    r = client.post("/admin/ingest", json={"source": "scraper", "slugs": ["pm-kisan"]},
                    headers={"X-Admin-Token": "test-admin"})
    assert r.status_code == 202 and calls == [["pm-kisan"]]
    status = client.get("/admin/ingest", headers={"X-Admin-Token": "test-admin"}).json()
    assert status["last"]["ok"] is True


def test_voice_without_provider_uses_transcript(client):
    r = client.post("/voice", files={"audio": ("a.webm", b"", "audio/webm")},
                    data={"transcript": "PM Kisan mein kitne paise milte hain?", "speak": "false"})
    assert r.status_code == 200 and r.json()["transcript"].startswith("PM Kisan")
    r = client.post("/voice", files={"audio": ("a.webm", b"xx", "audio/webm")})
    assert r.status_code == 503


def test_rate_limit(store):
    from backend.core.ratelimit import RateLimitMiddleware
    from fastapi import FastAPI

    mini = FastAPI()
    mini.add_middleware(RateLimitMiddleware, per_minute=2)

    @mini.post("/search")
    def _s():
        return {"ok": True}

    c = TestClient(mini)
    codes = [c.post("/search").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
