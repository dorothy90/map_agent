"""Live model evaluation; fixture domain registries must identify data_origin."""
import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from langgraph.checkpoint.mongodb import MongoDBSaver

from harness.config import Settings
from harness.control import RunController
from harness.store import HarnessStore


async def evaluate(queries, output, no_domain_tools=False, *, registry_factory=None, data_origin="live"):
    load_dotenv()
    settings = Settings.from_env()
    store = HarnessStore(settings.mongo_uri, "harness_eval_" + uuid.uuid4().hex)
    report = {"runtime": "harness/v2", "model": settings.model, "model_origin": "live", "database": store.db.name, "data_origin": data_origin, "cases": []}
    report["generation"] = {"temperature": settings.temperature, "max_output_tokens": settings.max_output_tokens, "reasoning_effort": settings.reasoning_effort}
    from harness.evals.run import capture_build_identity
    report["build"] = capture_build_identity(model={"model": settings.model, "base_url": settings.base_url, **report["generation"]})
    await store.setup()
    with MongoDBSaver.from_conn_string(settings.mongo_uri, db_name=store.db.name,
            checkpoint_collection_name="harness_checkpoints", writes_collection_name="harness_checkpoint_writes") as saver:
        from harness.tools.registry import ToolRegistry
        factory = registry_factory or (ToolRegistry if no_domain_tools else None)
        controller = RunController(store, settings, saver, **({"registry_factory": factory} if factory else {}))
        try:
            current_run = None
            for turn in queries:
                action = turn.get("action", "query") if isinstance(turn, dict) else "query"
                value = turn.get("value") if isinstance(turn, dict) else turn
                started = time.monotonic()
                if action == "query":
                    run = await controller.start("live-eval", "conversation", str(uuid.uuid4()), value)
                elif action == "input":
                    if not current_run or current_run.get("status") != "waiting_user":
                        raise ValueError("input action requires a waiting_user run")
                    run = await controller.submit_input("live-eval", current_run["run_id"], {
                        "kind": "input", "request_id": str(uuid.uuid4()), "goal_revision": current_run["goal_revision"],
                        "interrupt_id": current_run.get("question", {}).get("interrupt_id"), "value": value})
                elif action == "steer":
                    if not current_run or not current_run.get("active"):
                        raise ValueError("steer action requires an active run")
                    run = await controller.submit_input("live-eval", current_run["run_id"], {
                        "kind": "steer", "request_id": str(uuid.uuid4()), "goal_revision": current_run["goal_revision"], "value": value})
                elif action == "cancel":
                    if not current_run:
                        raise ValueError("cancel action requires a run")
                    run = await controller.cancel("live-eval", current_run["run_id"])
                else:
                    raise ValueError(f"Unsupported evaluation action: {action}")
                await controller.tasks[run["run_id"]]
                run = await store.get_run("live-eval", run["run_id"])
                current_run = run
                unfinished = run["status"] in ("created", "running", "cancelling")
                if unfinished:
                    run = await controller.cancel("live-eval", run["run_id"])
                calls = await store.calls.find({"run_id": run["run_id"]}, {"_id": 0}).to_list(None)
                case = {k: run.get(k) for k in ("run_id", "query", "status", "answer", "stop_reason", "question", "usage", "observations", "goal_revision", "active", "events", "runtime_version", "skill_versions")}
                case.update(action=action, elapsed=round(time.monotonic() - started, 2), calls=calls)
                if unfinished:
                    case["evaluation_error"] = "nonterminal_worker_exit"
                report["cases"].append(case)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
                print(json.dumps({k: case[k] for k in ("query", "status", "stop_reason", "answer", "elapsed")}, ensure_ascii=False), flush=True)
                # Keep waiting runs alive for an explicit next input/steer action.
        finally:
            await controller.close()
            store.client.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-domain-tools", action="store_true", help="Disable all DB/domain tools for model-only checks")
    parser.add_argument("--allow-external-data", action="store_true")
    args = parser.parse_args()
    if not args.no_domain_tools and not args.allow_external_data:
        parser.error("Actual DB results go to the model; --allow-external-data is required")
    report = asyncio.run(evaluate(args.query, args.output, args.no_domain_tools))
    raise SystemExit(0 if all(c["status"] == "completed" for c in report["cases"]) else 1)
