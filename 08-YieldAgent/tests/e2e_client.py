"""E2E client + trace reader for yield-agent regression tests.

These tests drive the REAL running agent server (`uvicorn agent_server:app --port 8001`)
end-to-end and assert on the structured `traces/*.jsonl` events plus the SSE output.

Why trace-based assertions: the planner is an LLM, so exact wording varies. We assert on
semantic OUTCOMES that the trace records deterministically — which agent was planned, what
slots the planner produced (e.g. lotcd vs unit), whether a HITL missing-param block fired —
which is exactly what regressed when "4SS" was misfiled into the `unit` slot.

Runnable two ways:
  - pytest:    pytest tests/test_e2e_regression.py
  - standalone: python tests/test_e2e_regression.py
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx

BASE_URL = os.getenv("YIELD_AGENT_BASE_URL", "http://127.0.0.1:8001")
# trace dir lives next to this tests/ dir, under 08-YieldAgent/traces/
_AGENT_DIR = Path(__file__).resolve().parent.parent
TRACE_GLOB = os.getenv("YIELD_AGENT_TRACE_GLOB", str(_AGENT_DIR / "traces" / "*.jsonl"))

VALID_UNITS = {"weekly", "monthly", "daily"}


def trace_id_for(session_id: str) -> str:
    """Mirror local_trace.make_trace_id(session_id): trace_<sha256(seed)[:16]>."""
    return "trace_" + hashlib.sha256(session_id.encode("utf-8", "replace")).hexdigest()[:16]


def server_is_up(timeout: float = 3.0) -> bool:
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


@dataclass
class TurnResult:
    session_id: str
    trace_id: str
    sse_events: list[dict] = field(default_factory=list)
    sse_blob: str = ""               # all SSE event JSON concatenated, for substring search
    trace_events: list[dict] = field(default_factory=list)
    turns: list = field(default_factory=list)   # all TurnResults of a multi-turn case (set by run_case)

    # ── trace-derived structured views ────────────────────────────
    def events_of(self, event_type: str) -> list[dict]:
        return [e for e in self.trace_events if e.get("event_type") == event_type]

    def planner_requests(self) -> list[dict]:
        evs = self.events_of("planner_output")
        if not evs:
            return []
        return evs[-1].get("payload", {}).get("canonical_requests", []) or []

    def planned_agents(self) -> list[str]:
        return [r.get("agent", "") for r in self.planner_requests()]

    def slots_for(self, agent: str) -> dict:
        for r in self.planner_requests():
            if r.get("agent") == agent:
                return r.get("slots", {}) or {}
        return {}

    def missing_param_blocks(self) -> list[dict]:
        """hitl_triggered events caused by a required param being missing."""
        out = []
        for e in self.events_of("hitl_triggered"):
            p = e.get("payload", {})
            if p.get("reason") == "required_param_missing":
                out.append(p)
        return out

    def sse_interrupts(self, interrupt_type: str | None = None) -> list[dict]:
        """SSE interrupt events (graph paused for human input). The supervisor
        dispatch guard emits interrupt_type='missing_param' when a required slot
        is empty; plan_review emits interrupt_type='plan_review'."""
        out = [e for e in self.sse_events if e.get("type") == "interrupt"]
        if interrupt_type is not None:
            out = [e for e in out if e.get("interrupt_type") == interrupt_type]
        return out

    def cap_status_fired(self) -> bool:
        """True if the planner's _MAX_TASKS cap dropped requests this turn."""
        return self.sse_contains("처음") and self.sse_contains("개만") or self.sse_contains("너무 많아")

    def validation_issues(self) -> list[dict]:
        return [e.get("payload", {}) for e in self.events_of("validation_issue")]

    def sse_contains(self, needle: str) -> bool:
        return needle in self.sse_blob

    def displayed_lots(self) -> list[str]:
        """4SS-product lot ids (7-char) in displayed order from this turn's SSE.
        Used to know what an ordinal reference ("첫번째") should resolve to."""
        seen: list[str] = []
        for tok in re.findall(r"\b4SS[A-Z0-9]{4}\b", self.sse_blob):
            if tok not in seen:
                seen.append(tok)
        return seen

    def dispatched_param(self, agent: str, param: str) -> dict:
        """Param meta ({count, present, preview, type}) actually dispatched to an
        agent, from the supervisor_dispatch trace — this reflects chained auto-fill
        applied at dispatch (which planner_output slots do NOT show)."""
        for e in self.events_of("supervisor_dispatch"):
            p = e.get("payload", {})
            if p.get("target") == agent:
                return (p.get("params", {}) or {}).get(param, {}) or {}
        return {}


def _drain_sse(query: str, session_id: str, timeout: float, resume_value: str | None = None) -> tuple[list[dict], str]:
    events: list[dict] = []
    payload: dict = {"query": query, "session_id": session_id}
    if resume_value is not None:
        payload["resume_value"] = resume_value
    with httpx.Client(timeout=httpx.Timeout(timeout, read=timeout)) as client:
        with client.stream("POST", f"{BASE_URL}/chat/stream", json=payload) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[len("data:"):].strip()
                if not raw:
                    continue
                try:
                    events.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    blob = "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
    return events, blob


def _load_trace_events(trace_id: str) -> list[dict]:
    rows: list[dict] = []
    for path in glob.glob(TRACE_GLOB):
        try:
            with open(path, encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line or trace_id not in line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("trace_id") == trace_id:
                        rows.append(ev)
        except FileNotFoundError:
            continue
    return rows


class Session:
    """A live chat session. Reuse across turns to test multi-turn follow-ups
    (references like "첫번째 결과"). trace_id is deterministic from session_id, so
    turns within one session share it; we isolate each turn's events by event_id
    snapshot (events appended that weren't seen before this turn)."""

    def __init__(self, session_id: str | None = None):
        self.session_id = session_id or str(uuid.uuid4())
        self.trace_id = trace_id_for(self.session_id)
        self._seen: set[str] = {
            str(e.get("event_id")) for e in _load_trace_events(self.trace_id)
        }

    def turn(self, query: str, *, resume_value: str | None = None, timeout: float = 240.0) -> TurnResult:
        sse_events, sse_blob = _drain_sse(query, self.session_id, timeout, resume_value=resume_value)

        new_events: list[dict] = []
        for _ in range(10):
            allev = _load_trace_events(self.trace_id)
            new_events = [e for e in allev if str(e.get("event_id")) not in self._seen]
            done = any(e.get("event_type") == "planner_output" for e in new_events)
            if done or (resume_value and new_events):
                break
            time.sleep(0.3)
        self._seen.update(str(e.get("event_id")) for e in new_events)

        return TurnResult(
            session_id=self.session_id,
            trace_id=self.trace_id,
            sse_events=sse_events,
            sse_blob=sse_blob,
            trace_events=new_events,
        )


def run_turn(query: str, *, timeout: float = 240.0) -> TurnResult:
    """Single fresh-session turn (convenience for single-turn cases)."""
    return Session().turn(query, timeout=timeout)


def coerce_periods(value) -> int | None:
    """3, "3", "3w", "3주" -> 3 ; non-numeric -> None. Mirrors the runtime int guard."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return int(digits) if digits else None
