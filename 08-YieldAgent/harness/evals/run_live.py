"""Run live and seeded scenarios through the stock Hermes chat controller."""
import argparse
import asyncio
import json
import time
import tempfile
import uuid
from pathlib import Path

from dotenv import load_dotenv

from harness.config import Settings
from harness.control import RunController
from harness.gateway.pending_controls import PendingControls
from harness.store import HarnessStore


async def evaluate(queries, output, no_domain_tools=False, *, fixture_rows=None, data_origin="live"):
    load_dotenv()
    settings = Settings.from_env()
    store = HarnessStore(settings.mongo_uri, "harness_eval_" + uuid.uuid4().hex)
    report = {"runtime": "harness/v2", "model": settings.model, "model_origin": "live", "database": store.db.name, "data_origin": data_origin, "cases": []}
    report["generation"] = {"temperature": settings.temperature, "max_output_tokens": settings.max_output_tokens, "reasoning_effort": settings.reasoning_effort}
    from harness.evals.run import capture_build_identity
    report["build"] = capture_build_identity(model={"model": settings.model, "base_url": settings.base_url, **report["generation"]})
    await store.setup()
    principal = "eval-" + uuid.uuid4().hex
    session = uuid.uuid4().hex
    report.update(principal=principal, session_id=session)
    restricted = no_domain_tools or fixture_rows is not None
    shared = tempfile.TemporaryDirectory(prefix="yield-eval-") if restricted else None
    if shared:
        allowed = ["read_result", "execute_code"] if fixture_rows is not None else []
        settings = settings.model_copy(update={"native_allowed_tools": allowed, "shared_yield_dir": Path(shared.name).resolve()})
    controller = RunController(store, settings)
    pending = PendingControls(store, principal)
    current_run = None
    try:
        source = None
        if fixture_rows is not None:
            source = await store.put_result(principal, session, {
                "run_id": session, "invocation_id": "fixture", "tool_name": "fixture",
                "provenance": {"data_origin": "fixture"}}, fixture_rows, [])
            report.update(data_origin="fixture", fixture_result_id=source.result_id)
        prompt = None
        for index, turn in enumerate(queries):
            action = turn.get("action", "query") if isinstance(turn, dict) else "query"
            value = turn.get("value") if isinstance(turn, dict) else turn
            started = time.monotonic()
            if action == "query":
                if source is not None and index == 0:
                    value = f"가상 평가 자료의 저장된 결과 ID는 {source.result_id}입니다.\n{value}"
                run = await controller.start(principal, session, str(uuid.uuid4()), value)
            elif action == "input":
                if not current_run or not prompt:
                    raise ValueError("input action requires an unanswered Hermes question")
                await pending.respond(session, current_run["epoch"], prompt["prompt_id"], {"answer": value}, "clarify")
                run = current_run
            elif action == "steer":
                if not current_run or not current_run.get("active"):
                    raise ValueError("steer action requires an active run")
                run = await controller.submit_input(principal, current_run["run_id"], {
                    "kind": "steer", "request_id": str(uuid.uuid4()), "goal_revision": current_run["goal_revision"], "value": value})
            elif action == "cancel":
                if not current_run:
                    raise ValueError("cancel action requires a run")
                run = await controller.cancel(principal, current_run["run_id"])
            else:
                raise ValueError(f"Unsupported evaluation action: {action}")
            current_run = run
            task = controller.tasks[run["run_id"]]
            prompt = None
            following = queries[index + 1] if index + 1 < len(queries) else None
            next_action = following.get("action") if isinstance(following, dict) else None
            if next_action not in ("steer", "cancel"):
                while not task.done():
                    run = await store.get_run(principal, run["run_id"])
                    prompts = await pending.snapshot(session, run["epoch"]) if run["epoch"] else []
                    prompt = next((p for p in prompts if p["kind"] == "clarify"), None)
                    if prompt:
                        break
                    await asyncio.wait({task}, timeout=.1)
            if next_action not in ("cancel", "steer") and not prompt and not task.cancelled():
                await task
            run = await store.get_run(principal, run["run_id"])
            current_run = run
            unfinished = task.done() and run["status"] in ("created", "running", "cancelling")
            unanswered = prompt is not None and next_action != "input"
            if unfinished or unanswered:
                run = await controller.cancel(principal, run["run_id"])
            calls = await store.calls.find({"run_id": run["run_id"]}, {"_id": 0}).to_list(None)
            case = {k: run.get(k) for k in ("run_id", "query", "status", "answer", "stop_reason", "question", "usage", "observations", "goal_revision", "active", "events", "runtime_version", "skill_reads")}
            case.update(native_engine=run.get("native_engine"), action=action, elapsed=round(time.monotonic() - started, 2), calls=calls)
            if prompt:
                case["question"] = prompt
            if unfinished or unanswered:
                case["evaluation_error"] = "nonterminal_worker_exit" if unfinished else "unanswered_clarification"
            report["cases"].append(case)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            print(json.dumps({k: case[k] for k in ("query", "status", "stop_reason", "answer", "elapsed")}, ensure_ascii=False), flush=True)

    finally:
        if current_run and current_run.get("active"):
            await controller.cancel(principal, current_run["run_id"])
        await controller.close()
        store.client.close()
        if shared:
            shared.cleanup()
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
