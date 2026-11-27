"""Langfuse tracing without the SDK: one trace per request, one span per pipeline step.

Spans are exported to Langfuse's OpenTelemetry endpoint when the request ends.
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
    """Sends a finished trace to Langfuse's OpenTelemetry endpoint (OTLP over HTTP/JSON).

    The legacy /api/public/ingestion endpoint now answers 410 Gone, so traces go through
    /api/public/otel/v1/traces: one root span (the request) and one child span per pipeline step.
    """

    def __init__(self, host: str, public_key: str, secret_key: str) -> None:
        self.url = host.rstrip("/") + "/api/public/otel/v1/traces"
        self.auth = (public_key, secret_key)

    @staticmethod
    def _attrs(d: Dict[str, Any]) -> List[Dict[str, Any]]:
        import json as _json
        out = []
        for k, v in d.items():
            if v is None:
                continue
            sv = v if isinstance(v, str) else _json.dumps(v, ensure_ascii=False, default=str)
            out.append({"key": k, "value": {"stringValue": sv[:20000]}})
        return out

    def _payload(self, t: Trace) -> Dict[str, Any]:
        trace_id = uuid.UUID(t.id).hex
        root_id = uuid.uuid4().hex[:16]
        end = time.time()
        ns = lambda x: str(int(x * 1e9))  # noqa: E731
        spans = [{
            "traceId": trace_id, "spanId": root_id, "name": t.name, "kind": 2,
            "startTimeUnixNano": ns(t.start), "endTimeUnixNano": ns(end),
            "attributes": self._attrs({
                "langfuse.trace.name": t.name, "langfuse.session.id": t.session_id,
                "langfuse.trace.input": t.input, "langfuse.trace.output": t.output,
                "langfuse.trace.metadata": t.metadata,
            }),
        }]
        for s in t.spans:
            attrs = {"langfuse.observation.input": s.get("input"), "langfuse.observation.output": s.get("output")}
            if s.get("level"):
                attrs.update({"langfuse.observation.level": s["level"],
                              "langfuse.observation.status_message": s.get("status")})
            spans.append({
                "traceId": trace_id, "spanId": uuid.uuid4().hex[:16], "parentSpanId": root_id, "name": s["name"],
                "kind": 1, "startTimeUnixNano": ns(s["start"]), "endTimeUnixNano": ns(s.get("end") or end),
                "attributes": self._attrs(attrs),
            })
        return {"resourceSpans": [{
            "resource": {"attributes": self._attrs({"service.name": "haqdar-api"})},
            "scopeSpans": [{"scope": {"name": "haqdar"}, "spans": spans}],
        }]}

    def send(self, trace: Trace) -> None:
        payload = self._payload(trace)

        def _post() -> None:
            try:
                r = httpx.post(self.url, json=payload, auth=self.auth, timeout=10.0)
                if r.status_code >= 300:
                    log.warning("langfuse otel export failed: %s %s", r.status_code, r.text[:200])
            except Exception as exc:  # noqa: BLE001
                log.debug("langfuse send failed: %s", exc)

        # Serverless functions may freeze right after the response: send inline there, in a thread elsewhere.
        import os
        if os.environ.get("VERCEL"):
            _post()
        else:
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
