"""Replay the saved multi-turn failure in an isolated DB with real domain tools.

No seed is sent to a provider unless --allow-external-data is passed. Original
runs/results remain unchanged. The evaluation DB is retained for source checks.
"""
import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from langgraph.checkpoint.mongodb import MongoDBSaver

from harness.config import Settings
from harness.context import build_context, session_context
from harness.control import RunController
from harness.executor import estimate_model_tokens
from harness.instructions import load_instructions
from harness.model import build_model
from harness.store import HarnessStore


async def evaluate(args):
    load_dotenv()
    settings = Settings.from_env()
    source = HarnessStore(settings.mongo_uri, settings.mongo_db)
    target = HarnessStore(settings.mongo_uri, "harness_context_eval_" + uuid.uuid4().hex)
    report = {"database": target.db.name, "model": settings.model, "token_limit": settings.token_limit,
        "context_tokens": settings.context_tokens, "seed_run_id": args.seed_run, "data_origin": "live", "cases": []}
    try:
        failed = await source.get_run(settings.principal, args.seed_run)
        principal, session = failed["principal_id"], failed["session_id"]
        previous = await source.runs.find({"principal_id": principal, "session_id": session,
            "active": False, "created_at": {"$lt": failed["created_at"]}}).sort("created_at", -1).limit(10).to_list(10)
        history, observations, focus = session_context(previous)
        from langchain_core.messages import HumanMessage
        state = {"run_id": "evaluation", "goal": {"original_request": failed["query"]},
            "messages": [*history, HumanMessage(content=failed["query"])], "observations": observations, "focus_result_ids": focus}
        report["seed_turns"] = len(previous)
        report["first_context_estimated_tokens_without_tools"] = estimate_model_tokens(build_model(settings), build_context(state, load_instructions()), 0)
        if not args.allow_external_data:
            print(json.dumps(report, ensure_ascii=False), flush=True)
            return report
        await target.setup()
        # Copy full owned results and their lineage, including artifact bytes.
        pending = [o["result_id"] for r in previous for o in r.get("observations", [])]
        copied = {}
        while pending:
            rid = pending.pop()
            if rid in copied:
                continue
            obs = await source.observation(principal, session, rid)
            rows = await source.rows(principal, session, rid)
            artifacts = []
            for artifact in obs.artifact_refs:
                ref, data = await source.artifact(principal, session, artifact["artifact_id"])
                artifacts.append({"title": ref["title"], "mime": ref["mime"], "data": data})
            supplied = obs.model_dump(exclude={"principal_id", "session_id", "result_id", "data_ref", "preview_rows", "columns", "total_rows", "truncated", "artifact_refs"})
            clone = await target.put_result(principal, session, supplied, rows, artifacts)
            assert clone.result_id == rid
            copied[rid] = clone.model_dump(mode="json")
            pending.extend(obs.source_result_ids)
        for old in previous:
            old["observations"] = [copied[o["result_id"]] for o in old.get("observations", [])]
            old["events"] = []
            old["evaluation_seed"] = True
            await target.runs.insert_one(old)
        report["copied_results"] = len(copied)
        with MongoDBSaver.from_conn_string(settings.mongo_uri, db_name=target.db.name,
                checkpoint_collection_name="harness_checkpoints", writes_collection_name="harness_checkpoint_writes") as saver:
            controller = RunController(target, settings, saver)
            try:
                for query in [failed["query"], *args.followup]:
                    started = time.monotonic()
                    run = await controller.start(principal, session, uuid.uuid4().hex, query)
                    await controller.tasks[run["run_id"]]
                    run = await target.get_run(principal, run["run_id"])
                    calls = await target.calls.find({"run_id": run["run_id"]}, {"_id": 0}).to_list(None)
                    usage = [e["payload"] for e in run["events"] if e["type"] == "model_usage"]
                    case = {k: run.get(k) for k in ("run_id", "query", "status", "stop_reason", "answer", "answer_text", "result_ids", "usage", "observations", "question")}
                    case.update(elapsed=round(time.monotonic() - started, 2), calls=calls, model_usage=usage,
                        input_tokens=sum(u.get("input_tokens") or 0 for u in usage), output_tokens=sum(u.get("output_tokens") or 0 for u in usage),
                        compactions=sum(u["purpose"] == "compaction" for u in usage))
                    report["cases"].append(case)
                    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
                    print(json.dumps({k: case[k] for k in ("run_id", "query", "status", "stop_reason", "input_tokens", "output_tokens", "compactions", "elapsed", "answer_text")}, ensure_ascii=False), flush=True)
                    if run["active"]:
                        await controller.cancel(principal, run["run_id"])
                        break
            finally:
                await controller.close()
        return report
    finally:
        source.client.close()
        target.client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-run", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--followup", action="append", default=[])
    parser.add_argument("--allow-external-data", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(evaluate(args))
    raise SystemExit(0 if all(c["stop_reason"] == "verified" for c in report["cases"]) else 1)
