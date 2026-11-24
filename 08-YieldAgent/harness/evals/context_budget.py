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

from bson import ObjectId
from dotenv import load_dotenv

from harness.config import Settings
from harness.control import RunController
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
        report["seed_turns"] = len(previous)
        if not args.allow_external_data:
            print(json.dumps(report, ensure_ascii=False), flush=True)
            return report
        await target.setup()
        # Use a new Hermes profile and session: its SQLite conversation survives
        # independently of the source DB, even if the real employee keeps chatting.
        eval_principal, eval_session = "context-eval-" + uuid.uuid4().hex, uuid.uuid4().hex
        report.update(principal=eval_principal, session_id=eval_session)
        scope = {"principal_id": principal, "session_id": session}
        destination = {"principal_id": eval_principal, "session_id": eval_session}
        pending = [o["result_id"] for turn in previous for o in turn.get("observations", [])]
        copied = set()
        while pending:
            rid = pending.pop()
            if rid in copied:
                continue
            record = await source.results.find_one({**scope, "result_id": rid})
            if record is None:
                raise LookupError(f"Source result {rid} is missing")
            obs = record["observation"]
            blob_ids = {t["data_ref"] for t in obs.get("tables", [])}
            blob_ids.update(a["artifact_id"] for a in obs.get("artifact_refs", []))
            if obs.get("data_ref"):
                blob_ids.add(obs["data_ref"])
            for blob in blob_ids:
                identity = ObjectId(blob)
                stream = await source.blobs.open_download_stream(identity)
                async with target.blobs.open_upload_stream_with_id(identity, stream.filename) as output:
                    while chunk := await stream.readchunk():
                        await output.write(chunk)
            await target.results.insert_one({**record, **destination, "observation": {**obs, **destination}})
            copied.add(rid)
            pending.extend(obs.get("source_result_ids", []))
        for old in previous:
            # These rows are replay snapshots. The new profile imports their
            # transcript once, then every follow-up resumes its own state.db.
            old["seed_native_engine"] = old.pop("native_engine", None)
            old.update(destination, evaluation_seed=True, events=[])
            old["observations"] = [{**o, **destination} for o in old.get("observations", [])]
            await target.runs.insert_one(old)
        report["copied_results"] = len(copied)
        controller = RunController(target, settings)
        try:
            for query in [failed["query"], *args.followup]:
                started = time.monotonic()
                run = await controller.start(eval_principal, eval_session, uuid.uuid4().hex, query)
                await controller.tasks[run["run_id"]]
                run = await target.get_run(eval_principal, run["run_id"])
                calls = await target.calls.find({"run_id": run["run_id"]}, {"_id": 0}).to_list(None)
                usage = [e["payload"] for e in run["events"] if e["type"] == "model_usage"]
                case = {k: run.get(k) for k in ("run_id", "query", "status", "stop_reason", "answer", "answer_text", "result_ids", "usage", "observations", "question")}
                case.update(native_engine=run.get("native_engine"), elapsed=round(time.monotonic() - started, 2), calls=calls, model_usage=usage,
                    input_tokens=(run.get("native_usage") or {}).get("input_tokens"), output_tokens=(run.get("native_usage") or {}).get("output_tokens"),
                    compactions=sum(u.get("purpose") == "compaction" for u in usage) if usage else None)
                report["cases"].append(case)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
                print(json.dumps({k: case[k] for k in ("run_id", "query", "status", "stop_reason", "input_tokens", "output_tokens", "compactions", "elapsed", "answer_text")}, ensure_ascii=False), flush=True)
                if run["active"]:
                    await controller.cancel(eval_principal, run["run_id"])
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
    raise SystemExit(0 if all(c["status"] == "completed" and c["native_engine"]["entrypoint"] == "stock_chat" for c in report["cases"]) else 1)
