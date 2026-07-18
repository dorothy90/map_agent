from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from job_models import JobSnapshot


MAX_EVENT_BYTES = 256 * 1024


@dataclass(frozen=True)
class StoredEvent:
    id: str
    data: dict[str, Any]


class JobEventStore:
    def __init__(self, redis, ttl_seconds: int, max_events: int):
        self.redis = redis
        self.ttl_seconds = ttl_seconds
        self.max_events = max_events

    @staticmethod
    def _key(job_id: str) -> str:
        return f"job:events:{job_id}"

    async def publish(self, job_id: str, event: dict[str, Any]) -> str:
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        if len(payload.encode("utf-8")) > MAX_EVENT_BYTES:
            raise ValueError("event payload exceeds 256 KiB")

        key = self._key(job_id)
        event_id = await self.redis.xadd(
            key,
            {"payload": payload},
            maxlen=self.max_events,
            approximate=True,
        )
        await self.redis.expire(key, self.ttl_seconds)
        return event_id.decode() if isinstance(event_id, bytes) else event_id

    async def read(
        self, job_id: str, after_id: str, block_ms: int
    ) -> list[StoredEvent]:
        streams = await self.redis.xread(
            {self._key(job_id): after_id},
            block=block_ms,
        )
        events: list[StoredEvent] = []
        for _, entries in streams:
            for event_id, fields in entries:
                raw_payload = fields.get("payload", fields.get(b"payload"))
                if isinstance(raw_payload, bytes):
                    raw_payload = raw_payload.decode("utf-8")
                if isinstance(event_id, bytes):
                    event_id = event_id.decode()
                events.append(StoredEvent(id=event_id, data=json.loads(raw_payload)))
        return events

    def snapshot_event(self, job: dict[str, Any]) -> dict[str, Any]:
        snapshot = JobSnapshot.model_validate(job).model_dump(mode="json")
        return {"type": "job_snapshot", **snapshot}

    async def expire(self, job_id: str) -> bool:
        return bool(await self.redis.expire(self._key(job_id), self.ttl_seconds))
