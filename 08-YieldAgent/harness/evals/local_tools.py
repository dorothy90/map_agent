"""Real domain tools with deterministic assertions; no model or embedding API."""
import asyncio
import json
import logging
import os
import uuid
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from harness.config import Settings
from harness.executor import ExecutionContext, ToolExecutor
from harness.store import HarnessStore
from harness.tools.registry import domain_registry


def _map_sample():
    from common import get_oracle_connection
    from map_agent import ORACLE_TABLE
    with get_oracle_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT LOT_ID, OPER_DET_DESC FROM {ORACLE_TABLE} WHERE LOT_CD = :product AND OPER_DET_DESC IN ('PT1H TEST', 'PT1C TEST') FETCH FIRST 1 ROWS ONLY", {"product": "4SS"})
            row = cursor.fetchone()
            return {"lot_ids": [row[0]], "oper": {"PT1H TEST": "PT1H", "PT1C TEST": "PT1C"}[row[1]]} if row else None


async def evaluate():
    load_dotenv()
    # Imports in legacy domain modules load .env; disable tracing after loading them.
    import common, yield_db, wads_tools, lot_history_tools, map_agent, fail_history_tools, relation_tree_agent, wt_resp_agent
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
    settings = Settings.from_env()
    store = HarnessStore(settings.mongo_uri, "harness_domain_eval_" + uuid.uuid4().hex)
    await store.setup()
    run = await store.start_run("local-eval", "s", "r", "Local domain verification")
    run = await store.acquire(run["run_id"], "eval")
    executor = ToolExecutor(domain_registry(), ExecutionContext(store, settings, run))
    report = {"database": store.db.name, "data_origin": "live", "external_model_calls": 0, "tools": []}
    async def pulse():
        while True:
            await asyncio.sleep(5)
            await store.renew(run["run_id"], run["epoch"], 5)
    heartbeat = asyncio.create_task(pulse())
    async def call(name, args):
        obs = await executor.execute({"name": name, "args": args, "id": uuid.uuid4().hex})
        item = {"name": name, "status": obs.status, "rows": obs.total_rows, "artifacts": len(obs.artifact_refs), "result_id": obs.result_id}
        if obs.error:
            item["error"] = obs.error
        report["tools"].append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
        return obs
    try:
        today = date.today()
        y = await call("query_yield", {"lotcd": "4SS", "ref_date": today.isoformat(), "periods": 4})
        assert y.status == "success" and y.total_rows == 4
        full = await store.rows("local-eval", "s", y.result_id)
        p = await call("run_python", {"input_result_ids": [y.result_id], "code": "emit_table(pd.DataFrame([{'row_count':len(df)}])); emit_plot(px.bar(pd.DataFrame({'period':range(len(df)), 'observations':[1]*len(df)}), x='period', y='observations'))"})
        assert p.status == "success" and p.preview_rows[0]["row_count"] == len(full) and len(p.artifact_refs) == 2
        wads_args = {"lotcd": "4SS", "start_tm": (today - timedelta(days=27)).isoformat(), "end_tm": today.isoformat()}
        w = await call("query_wads", wads_args)
        assert w.status in ("success", "empty")
        refs = [y.result_id]
        if w.total_rows:
            first = (await store.rows("local-eval", "s", w.result_id))[0]
            parameter, category = first["parameter"], "PT1H" if str(first["category"]).startswith("PT1H") else "PT1C"
            r = await call("get_wads_report", {**wads_args, "parameter": parameter, "category": category})
            refs.append(r.result_id)
            relation_args = {"lotcd": "4SS", "fail_type": parameter, "category": category}
            await call("query_relation", relation_args)
            groups = await call("query_good_bad_groups", relation_args)
            lots = [row["lot_id"] for row in await store.rows("local-eval", "s", groups.result_id)]
            if lots:
                await call("query_lot_history", {"lot_ids": lots[:2]})
                m = await call("render_wafer_map", {"lot_ids": lots[:1], "oper": category})
                if m.artifact_refs:
                    refs.append(m.result_id)
            else:
                report["blocked"] = "No actual good/bad LOTs for selected report"
        else:
            report["blocked"] = "No actual WADS rows for current period"
        await call("search_fail_history", {"query": "수율", "product": "4SS"})
        from harness.runtime.domain_process import run_blocking
        sample = await run_blocking(_map_sample)
        if sample:
            sample_map = await call("render_wafer_map", sample)
            assert sample_map.artifact_refs and sample_map.status == "success"
            refs.append(sample_map.result_id)
        else:
            report["blocked"] = "No actual wafer-map sample for the product"
        ppt = await call("export_report", {"result_ids": refs})
        if ppt.artifact_refs:
            import io
            from pptx import Presentation
            _, data = await store.artifact("local-eval", "s", ppt.artifact_refs[0]["artifact_id"])
            report["ppt_slides"] = len(Presentation(io.BytesIO(data)).slides)
        report["mining"] = "not_configured" if not os.getenv("HARNESS_MINING_API_URL") else "requires_live_contract_verification"
        report["ok"] = all(tool["status"] in ("success", "empty", "partial") for tool in report["tools"]) and not report.get("blocked")
        await store.finish(run["run_id"], run["epoch"], "completed" if report["ok"] else "partial", "Local verification", "evaluation")
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        output = Path("outputs/harness-local-tools.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        store.client.close()
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    report = asyncio.run(evaluate())
    raise SystemExit(0 if report.get("ok") else 1)
