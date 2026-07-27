from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field


@dataclass
class RunControl:
    session_id: str
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)


class RunRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, RunControl] = {}
        self._lock = threading.Lock()

    def register(self, run_id: str, session_id: str) -> RunControl:
        control = RunControl(session_id=session_id)
        with self._lock:
            if run_id in self._runs:
                raise ValueError(f"Run already registered: {run_id}")
            self._runs[run_id] = control
        return control

    def get(self, run_id: str) -> RunControl | None:
        with self._lock:
            return self._runs.get(run_id)

    def unregister(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)


run_registry = RunRegistry()
