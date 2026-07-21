from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from common import get_llm
from control_knowledge_curator import ControlKnowledgeCurator, CuratorCallError
from control_knowledge_models import KnowledgeCandidate
from control_knowledge_store import ControlKnowledgeStore

logger = logging.getLogger("yield_agent.control_knowledge")


def _enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


class ControlKnowledgeService:
    def __init__(
        self,
        store: ControlKnowledgeStore,
        curator: ControlKnowledgeCurator,
        *,
        enabled: bool,
        writer: bool,
        queue_size: int = 100,
        max_retries: int = 3,
        retry_base_seconds: float = 1.0,
    ):
        self.store = store
        self.curator = curator
        self.enabled = enabled
        self.writer = writer
        self.max_retries = max_retries
        self.retry_base_seconds = retry_base_seconds
        self.queue: asyncio.Queue[tuple[Path, int]] = asyncio.Queue(maxsize=queue_size)
        self.worker_task: asyncio.Task | None = None
        self.queued_fingerprints: set[str] = set()
        self.stats = {
            "persisted": 0,
            "queued": 0,
            "processed": 0,
            "retried": 0,
            "dropped": 0,
            "failed": 0,
        }

    async def start(self) -> None:
        if not self.enabled or not self.writer or self.worker_task is not None:
            return
        self.worker_task = asyncio.create_task(
            self._run(), name="control_knowledge_curator"
        )
        for path in self.store.pending_candidates():
            try:
                self.queue.put_nowait((path, 0))
                self.queued_fingerprints.add(path.stem)
            except asyncio.QueueFull:
                break

    async def submit(self, candidate: KnowledgeCandidate) -> str:
        if not self.enabled:
            return "disabled"
        path = await asyncio.to_thread(self.store.save_candidate, candidate)
        self.stats["persisted"] += 1
        if await asyncio.to_thread(self.store.is_processed, path.stem):
            return "processed"
        if path.stem in self.queued_fingerprints:
            return "queued"
        if not self.writer:
            return "persisted"
        try:
            self.queue.put_nowait((path, 0))
            self.queued_fingerprints.add(path.stem)
            self.stats["queued"] += 1
            return "queued"
        except asyncio.QueueFull:
            self.stats["dropped"] += 1
            logger.warning(
                "control knowledge queue full; candidate remains pending: %s", path.name
            )
            return "pending"

    async def _run(self) -> None:
        while True:
            path, attempt = await self.queue.get()
            requeued = False
            try:
                candidate = await asyncio.to_thread(self.store.load_candidate, path)
                await asyncio.to_thread(self.curator.curate, candidate)
                self.stats["processed"] += 1
            except CuratorCallError as exc:
                if attempt + 1 < self.max_retries:
                    self.stats["retried"] += 1
                    await asyncio.sleep(self.retry_base_seconds * (2**attempt))
                    await self.queue.put((path, attempt + 1))
                    requeued = True
                else:
                    self.stats["failed"] += 1
                    logger.warning(
                        "control curator unavailable after retries: %s",
                        type(exc).__name__,
                    )
            except Exception as exc:
                self.stats["failed"] += 1
                logger.warning("control candidate failed: %s", type(exc).__name__)
            finally:
                if not requeued:
                    self.queued_fingerprints.discard(path.stem)
                self.queue.task_done()

    async def stop(self, timeout: float = 10.0) -> None:
        if self.worker_task is None:
            return
        try:
            await asyncio.wait_for(self.queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("control knowledge drain timeout; pending files remain on disk")
        self.worker_task.cancel()
        try:
            await self.worker_task
        except asyncio.CancelledError:
            pass
        self.worker_task = None


def service_from_env() -> ControlKnowledgeService:
    root = Path(
        os.getenv(
            "CONTROL_KNOWLEDGE_ROOT",
            str(Path(__file__).resolve().parent / "multiagent_knowledge"),
        )
    )
    store = ControlKnowledgeStore(root)
    model = os.getenv("CONTROL_KNOWLEDGE_MODEL") or None
    curator = ControlKnowledgeCurator(store, get_llm(model))
    return ControlKnowledgeService(
        store,
        curator,
        enabled=_enabled("CONTROL_KNOWLEDGE_ENABLED"),
        writer=_enabled("CONTROL_KNOWLEDGE_WRITER"),
        queue_size=int(os.getenv("CONTROL_KNOWLEDGE_QUEUE_SIZE", "100")),
        max_retries=int(os.getenv("CONTROL_KNOWLEDGE_MAX_RETRIES", "3")),
    )
