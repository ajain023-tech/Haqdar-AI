"""Langfuse tracing without the SDK: one trace per request, one span per pipeline step.

Events are posted to Langfuse's public ingestion API in a background thread when the request ends.
With no LANGFUSE_* keys set every call is a no-op, so tracing never slows down or breaks a query.
Only eligibility fields and query text are traced — never names, phone numbers or Aadhaar.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

import httpx

from backend.core.config import get_settings

log = logging.getLogger(__name__)


def _ts(t: Optional[float] = None) -> str:
    return datetime.fromtimestamp(t or time.time(), tz=timezone.utc).isoformat()


class Trace:
    def __init__(self, name: str, input: Any = None, metadata: Optional[Dict[str, Any]] = None,
                 session_id: Optional[str] = None) -> None:
        self.id = str(uuid.uuid4())
        self.name = name
        self.input = input
        self.output: Any = None
        self.metadata = metadata or {}
        self.session_id = session_id
        self.start = time.time()
        self.spans: List[Dict[str, Any]] = []
        self.timings_ms: Dict[str, float] = {}

    @contextmanager
    def span(self, name: str, input: Any = None) -> Iterator[Dict[str, Any]]:
        rec: Dict[str, Any] = {"id": str(uuid.uuid4()), "name": name, "input": input, "output": None,
                               "start": time.time()}
        try:
            yield rec
        except Exception as exc:
            rec["level"] = "ERROR"
            rec["status"] = str(exc)
            raise
        finally:
            rec["end"] = time.time()
            self.timings_ms[name] = round((rec["end"] - rec["start"]) * 1000, 1)
            self.spans.append(rec)

    def end(self, output: Any = None) -> None:
        self.output = output
        _get_sink().send(self)


class _NoopSink:
    def send(self, trace: Trace) -> None:
        return None


class _LangfuseSink:
    def __init__(self, host: str, public_key: str, secret_key: str) -> None:
        self.url = host.rstrip("/") + "/api/public/ingestion"
        self.auth = (public_key, secret_key)

    def _batch(self, t: Trace) -> List[Dict[str, Any]]:
        events = [{
            "id": str(uuid.uuid4()), "type": "trace-create", "timestamp": _ts(t.start),
            "body": {"id": t.id, "name": t.name, "input": t.input, "output": t.output,
                     "metadata": t.metadata, "sessionId": t.session_id, "timestamp": _ts(t.start)},
        }]
        for s in t.spans:
            body = {"id": s["id"], "traceId": t.id, "name": s["name"], "input": s.get("input"),
                    "output": s.get("output"), "startTime": _ts(s["start"]), "endTime": _ts(s.get("end"))}
            if s.get("level"):
                body.update(level=s["level"], statusMessage=s.get("status"))
            events.append({"id": str(uuid.uuid4()), "type": "span-create", "timestamp": _ts(s["start"]),
                           "body": body})
        return events

    def send(self, trace: Trace) -> None:
        batch = self._batch(trace)

        def _post() -> None:
            try:
                httpx.post(self.url, json={"batch": batch}, auth=self.auth, timeout=10.0)
            except Exception as exc:  # noqa: BLE001
                log.debug("langfuse send failed: %s", exc)

        threading.Thread(target=_post, daemon=True).start()


_sink = None


def _get_sink():
    global _sink
    if _sink is None:
        s = get_settings()
        if s.langfuse_public_key and s.langfuse_secret_key:
            _sink = _LangfuseSink(s.langfuse_host, s.langfuse_public_key, s.langfuse_secret_key)
        else:
            _sink = _NoopSink()
    return _sink
