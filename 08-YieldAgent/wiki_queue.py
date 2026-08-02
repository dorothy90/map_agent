"""Wiki ingest 2단계 큐.

summarize_queue (LLM 호출 직렬화) → persist_queue (디스크 write 직렬화)
- 워커 각 1개 (single-writer 보장)
- 재시도 3회 지수 백오프, 초과 시 dropped + log
- lifespan start/stop, finally drain timeout
- placeholder summarizer (Day 3에 wiki_summarizer.summarize로 교체)
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
from typing import Any, Callable, Optional, TypedDict

import wiki_store
from lf_utils import reset_lf_capture_disabled, set_lf_capture_disabled

logger = logging.getLogger("yield_agent.wiki_queue")

SummarizeResult = dict[str, Any]
SummarizeFn = Callable[[dict[str, Any]], SummarizeResult | None]


class SummarizeQueueItem(TypedDict, total=False):
    task_type: str
    payload: dict[str, Any]
    attempt: int
    concept_id: str
    filters: dict[str, Any]
    episodes: list[dict[str, Any]]
    evidence: dict[str, Any]
    private: bool


class PersistQueueItem(TypedDict):
    kind: str
    args: tuple
    private: bool


def _placeholder_summarize(payload: dict[str, Any]) -> SummarizeResult | None:
    """Day 2 placeholder. Day 3에 wiki_summarizer.summarize로 교체."""
    raw = payload.get("raw_results") or []
    filters = payload.get("filters") or {}
    if not raw:
        return None
    first = raw[0]
    cause = (first.get("cause", "") or "")[:200]
    action = (first.get("action", "") or "")[:200]
    summary = (cause or first.get("comment", "") or "")[:120]
    return {
        "episode": {
            "query": payload.get("query", ""),
            "filters": filters,
            "doc_ids": [r.get("doc_id") or r.get("filenm") or "" for r in raw if (r.get("doc_id") or r.get("filenm"))],
            "body": f"## Placeholder Summary\n\n**원인**: {cause}\n\n**조치**: {action}\n",
            "summary": summary,
            "links": [],
        },
        "concept_filters": filters if all(filters.get(k) for k in ("product", "fail_type", "cause_oper")) else None,
        "alias_pairs": payload.get("alias_pairs") or [],
    }


class WikiQueue:
    """2단계 큐 + 워커 2개. lifespan에서 start/stop."""

    def __init__(self, summarize_fn: SummarizeFn | None = None, maxsize: int = 200, max_retry: int = 3):
        self._summarize_fn: SummarizeFn = summarize_fn or _placeholder_summarize
        self._maxsize = maxsize
        self._max_retry = max_retry
        self._summarize_q: asyncio.Queue[SummarizeQueueItem] | None = None
        self._persist_q: asyncio.Queue[PersistQueueItem] | None = None
        self._workers: list[asyncio.Task] = []
        self._running = False
        self.drops: dict[str, int] = {"summarize": 0, "persist": 0, "queue_full": 0,
                                       "synthesis": 0, "synthesis_skip_low_diversity": 0}
        self.commits: dict[str, int] = {"episode_created": 0, "episode_skipped": 0,
                                          "concept_created": 0, "concept_updated": 0,
                                          "alias_created": 0, "alias_skipped": 0,
                                          "synthesis_triggered": 0, "synthesis_persisted": 0}
        self._synth_in_flight: set[str] = set()  # concept_id 중복 트리거 가드

    # ── public API ─────────────────────────────────────
    def set_summarizer(self, fn: SummarizeFn) -> None:
        """Day 3에서 wiki_summarizer.summarize로 교체."""
        self._summarize_fn = fn

    def summarize_enqueue(self, payload: dict[str, Any], *, private: bool = False) -> str:
        """Enqueue with caller-provided privacy; never infer it from payload text."""
        if not self._running or self._summarize_q is None:
            return "skipped"
        # 64KB 가드
        try:
            import json
            if len(json.dumps(payload, ensure_ascii=False, default=str)) > 64 * 1024:
                payload = {**payload, "raw_results": (payload.get("raw_results") or [])[:3]}
        except Exception:
            pass
        try:
            self._summarize_q.put_nowait(
                {"payload": payload, "attempt": 0, "private": bool(private)}
            )
            return "queued"
        except asyncio.QueueFull:
            self.drops["queue_full"] += 1
            logger.warning("[wiki_queue] summarize queue full, dropped (drops=%d)", self.drops["queue_full"])
            return "dropped"

    async def start(self) -> None:
        if self._running:
            return
        self._summarize_q = asyncio.Queue(maxsize=self._maxsize)
        self._persist_q = asyncio.Queue(maxsize=self._maxsize * 2)
        self._workers = [
            asyncio.create_task(self._summarize_worker(), name="wiki_summarize_worker"),
            asyncio.create_task(self._persist_worker(), name="wiki_persist_worker"),
        ]
        self._running = True
        logger.info("[wiki_queue] started (maxsize=%d, max_retry=%d)", self._maxsize, self._max_retry)

    async def stop(self, timeout: float = 10.0) -> None:
        if not self._running:
            return
        self._running = False
        # drain 시도
        try:
            if self._summarize_q is not None:
                await asyncio.wait_for(self._summarize_q.join(), timeout=timeout / 2)
            if self._persist_q is not None:
                await asyncio.wait_for(self._persist_q.join(), timeout=timeout / 2)
        except asyncio.TimeoutError:
            remaining_s = self._summarize_q.qsize() if self._summarize_q else 0
            remaining_p = self._persist_q.qsize() if self._persist_q else 0
            logger.warning("[wiki_queue] drain timeout, dropping (summarize=%d, persist=%d)", remaining_s, remaining_p)
            self.drops["summarize"] += remaining_s
            self.drops["persist"] += remaining_p
        # cancel
        for t in self._workers:
            t.cancel()
        for t in self._workers:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._workers = []
        logger.info("[wiki_queue] stopped (drops=%s, commits=%s)", self.drops, self.commits)

    def stats(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "summarize_size": self._summarize_q.qsize() if self._summarize_q else 0,
            "persist_size": self._persist_q.qsize() if self._persist_q else 0,
            "drops": dict(self.drops),
            "commits": dict(self.commits),
        }

    # ── workers ────────────────────────────────────────
    async def _summarize_worker(self) -> None:
        assert self._summarize_q is not None and self._persist_q is not None
        while True:
            item = await self._summarize_q.get()
            # The lifespan worker predates requests, so restore privacy per item.
            capture_token = set_lf_capture_disabled(bool(item.get("private", False)))
            try:
                task_type = item.get("task_type", "episode_summarize")
                if task_type == "concept_synthesis":
                    await self._handle_concept_synthesis(item)
                else:
                    await self._handle_episode_summarize(item)
            finally:
                reset_lf_capture_disabled(capture_token)
                self._summarize_q.task_done()

    async def _handle_episode_summarize(self, item: dict[str, Any]) -> None:
        """기존 단일 검색 응축 (Day 3까지의 흐름)."""
        assert self._summarize_q is not None and self._persist_q is not None
        payload = item["payload"]
        attempt = item["attempt"]
        try:
            result = await self._call_summarize(payload)
        except Exception as e:
            logger.warning("[wiki_queue] summarize failed (attempt=%d): %s", attempt, e)
            result = None
        if result is None and attempt + 1 < self._max_retry:
            await asyncio.sleep(2 ** attempt)
            item["attempt"] = attempt + 1
            try:
                self._summarize_q.put_nowait(item)
            except asyncio.QueueFull:
                self.drops["summarize"] += 1
        elif result is None:
            self.drops["summarize"] += 1
            logger.warning("[wiki_queue] summarize dropped after %d attempts (drops=%d)",
                           self._max_retry, self.drops["summarize"])
        else:
            private = bool(item.get("private", False))
            ep = result.get("episode")
            if ep:
                self._persist_q.put_nowait(
                    {"kind": "episode", "args": (ep,), "private": private}
                )
            cf = result.get("concept_filters")
            if cf:
                self._persist_q.put_nowait(
                    {"kind": "concept", "args": (cf, None), "private": private}
                )
            for canonical, variant in (result.get("alias_pairs") or []):
                self._persist_q.put_nowait(
                    {
                        "kind": "alias",
                        "args": (canonical, variant),
                        "private": private,
                    }
                )

    async def _handle_concept_synthesis(self, item: dict[str, Any]) -> None:
        """plan v3 §A: evidence_diversity 통과한 concept을 LLM으로 메타 합성."""
        from wiki_summarizer import synthesize_concept

        concept_id = item["concept_id"]
        filters = item["filters"]
        episodes = item["episodes"]
        evidence = item["evidence"]

        try:
            result = await self._run_sync(synthesize_concept, concept_id, episodes)
        except Exception as e:
            logger.warning("[wiki_queue] synthesize_concept failed: %s", e)
            result = None
        self._synth_in_flight.discard(concept_id)
        if result is None:
            self.drops["synthesis"] = self.drops.get("synthesis", 0) + 1
            return
        synth_payload = {
            "body_markdown": result.body_markdown,
            "confidence": float(result.confidence),
            "citations": [c.model_dump() if hasattr(c, "model_dump") else c.dict() for c in result.citations],
            "evidence": evidence,
        }
        try:
            self._persist_q.put_nowait(
                {
                    "kind": "concept_synthesized",
                    "args": (filters, synth_payload),
                    "private": bool(item.get("private", False)),
                }
            )
        except asyncio.QueueFull:
            self.drops["queue_full"] += 1

    async def _persist_worker(self) -> None:
        assert self._persist_q is not None
        last_episode_id: Optional[str] = None
        while True:
            item = await self._persist_q.get()
            kind, args = item["kind"], item["args"]
            private = bool(item["private"])
            capture_token = set_lf_capture_disabled(private)
            try:
                attempt = 0
                while attempt < self._max_retry:
                    try:
                        if kind == "episode":
                            (ep_payload,) = args
                            eid, status = await self._run_sync(
                                wiki_store.upsert_episode, ep_payload
                            )
                            last_episode_id = f"episode:{eid}"
                            self.commits[f"episode_{status}"] = self.commits.get(f"episode_{status}", 0) + 1
                        elif kind == "concept":
                            cf, src_ep = args
                            src = src_ep or last_episode_id
                            cid, status = await self._run_sync(
                                wiki_store.upsert_concept, cf, src, None
                            )
                            self.commits[f"concept_{status}"] = self.commits.get(f"concept_{status}", 0) + 1
                            # plan v3 §A: concept update 후 evidence_diversity 트리거
                            await self._maybe_trigger_synthesis(
                                f"concept:{cid}", cf, private=private
                            )
                        elif kind == "concept_synthesized":
                            cf, synth = args
                            cid, status = await self._run_sync(
                                lambda: wiki_store.upsert_concept(
                                    cf, source_episode_id=None, links=None,
                                    synthesized_body=synth["body_markdown"],
                                    confidence=synth["confidence"],
                                    citations=synth["citations"],
                                    evidence=synth["evidence"],
                                ),
                            )
                            self.commits["synthesis_persisted"] += 1
                        elif kind == "alias":
                            canonical, variant = args
                            results = await self._run_sync(
                                wiki_store.upsert_alias, canonical, variant
                            )
                            for _, status in results:
                                self.commits[f"alias_{status}"] = self.commits.get(f"alias_{status}", 0) + 1
                        break
                    except Exception as e:
                        attempt += 1
                        logger.warning("[wiki_queue] persist %s failed (attempt=%d): %s", kind, attempt, e)
                        if attempt >= self._max_retry:
                            self.drops["persist"] += 1
                            logger.warning("[wiki_queue] persist %s dropped (drops=%d)", kind, self.drops["persist"])
                            break
                        await asyncio.sleep(2 ** attempt)
            finally:
                reset_lf_capture_disabled(capture_token)
                self._persist_q.task_done()

    async def _maybe_trigger_synthesis(
        self, concept_id: str, filters: dict[str, Any], *, private: bool
    ) -> None:
        """plan v3 §A: evidence_diversity 임계 통과 시 concept_synthesis task 발행.

        같은 raw 반복은 score 낮아 트리거 X (사용자 비판 정면 가드).
        """
        if concept_id in self._synth_in_flight:
            return  # 동시 트리거 가드
        def _load_and_check() -> tuple[list[dict], dict] | None:
            cid_key = concept_id.replace("concept:", "")
            cpath = wiki_store._CONCEPTS / f"{wiki_store._safe_filename(cid_key)}.md"
            cpost = wiki_store._read(cpath)
            if cpost is None:
                return None
            md = cpost.metadata
            src_ep_ids = list(md.get("source_episode_ids") or [])
            if len(src_ep_ids) < 2:
                return None
            episodes: list[dict[str, Any]] = []
            for ep_id in src_ep_ids[:10]:  # 토큰 가드
                key = ep_id.replace("episode:", "")
                ep_post = wiki_store._read(wiki_store._EPISODES / f"{key}.md")
                if ep_post:
                    episodes.append({
                        "id": ep_id,
                        "frontmatter": dict(ep_post.metadata),
                        "body": ep_post.content or "",
                    })
            if len(episodes) < 2:
                return None
            ev = wiki_store.compute_evidence_diversity(episodes)
            return episodes, ev

        try:
            check = await self._run_sync(_load_and_check)
        except Exception as e:
            logger.warning("[wiki_queue] synthesis check failed: %s", e)
            return
        if check is None:
            return
        episodes, evidence = check

        # 진단 가시성 — evidence 결과를 concept frontmatter에 항상 갱신
        await self._run_sync(
            wiki_store.update_concept_evidence, concept_id, evidence
        )

        # Karpathy 회귀: unique_doc_ids 가드 제거.
        # episode 2건 이상이면 합성 시도하되, evidence_diversity가 매우 낮은(<0.3)
        # 케이스(예: 동일 raw 5번 반복)만 차단 — Codex 핵심 우려 보존.
        if evidence["score"] < 0.3:
            self.drops["synthesis_skip_low_diversity"] += 1
            logger.info("[wiki_queue] synthesis skip — same-raw repetition %s: %s",
                        concept_id, evidence)
            return

        # 트리거 발행
        self._synth_in_flight.add(concept_id)
        try:
            self._summarize_q.put_nowait({
                "task_type": "concept_synthesis",
                "concept_id": concept_id,
                "filters": filters,
                "episodes": episodes,
                "evidence": evidence,
                "private": private,
            })
            self.commits["synthesis_triggered"] += 1
            logger.info("[wiki_queue] synthesis triggered %s (diversity=%s)",
                        concept_id, evidence["score"])
        except asyncio.QueueFull:
            self.drops["queue_full"] += 1
            self._synth_in_flight.discard(concept_id)

    async def _call_summarize(self, payload: dict[str, Any]) -> SummarizeResult | None:
        """summarize_fn이 sync든 async든 처리."""
        fn = self._summarize_fn
        if asyncio.iscoroutinefunction(fn):
            return await fn(payload)
        # sync 함수는 executor로 실행 (LLM 호출이 블로킹일 수 있음)
        return await self._run_sync(fn, payload)

    async def _run_sync(self, function, *args):
        """Run blocking queue work with the current item's privacy context."""

        loop = asyncio.get_running_loop()
        context = contextvars.copy_context()
        return await loop.run_in_executor(None, context.run, function, *args)


# ── 모듈 레벨 싱글톤 ────────────────────────────────────
wiki_queue = WikiQueue()


# ── smoke test (Day 2 검증용) ──────────────────────────
async def smoke(n: int = 100) -> dict[str, Any]:
    """N개 dummy enqueue → vault에 dedup된 상태로 영속.

    Run: `uv run python -c "import asyncio, sys; sys.path.insert(0,'.'); import wiki_queue; print(asyncio.run(wiki_queue.smoke(100)))"` (08-YieldAgent에서)
    """
    products = ["4SS", "4SA", "6E2"]
    cause_opers = ["STI CMP", "ILD CMP", "METAL1 DEP"]
    fail_types = ["EASY(W)", "FMAX(X)", "VMIN(M)"]
    await wiki_queue.start()
    try:
        for i in range(n):
            payload = {
                "query": f"smoke test query #{i}",
                "filters": {
                    "product": products[i % 3],
                    "cause_oper": cause_opers[i % 3],
                    "fail_type": fail_types[i % 3],
                },
                "raw_results": [{
                    "doc_id": f"FH-SMOKE-{i:04d}",
                    "cause": f"smoke cause #{i}",
                    "action": f"smoke action #{i}",
                }],
            }
            wiki_queue.summarize_enqueue(payload)
        # drain
        await asyncio.sleep(0.1)
        for _ in range(50):
            stats = wiki_queue.stats()
            if stats["summarize_size"] == 0 and stats["persist_size"] == 0:
                break
            await asyncio.sleep(0.1)
    finally:
        await wiki_queue.stop(timeout=10)
    return {"final_stats": wiki_queue.stats(), "vault_counts": wiki_store.counts()}
