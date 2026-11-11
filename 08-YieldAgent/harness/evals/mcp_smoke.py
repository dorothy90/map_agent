"""Actual stdio client + Oracle/Mongo. No external model receives the rows."""
import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check(ref_date):
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(root), "HARNESS_MONGO_DB": "harness_mcp_eval_" + uuid.uuid4().hex}
    params = StdioServerParameters(command=sys.executable, args=["-m", "harness.mcp_server"], cwd=str(root.parent), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert "query_wafer_yield" in {tool.name for tool in tools.tools}
            result = await session.call_tool("query_wafer_yield", {"lotcd": "4SS", "ref_date": ref_date, "periods": 4, "metric": "pass_rate", "measurement_policy": "all_measurements"})
            assert not result.isError, result.content
            obs = result.structuredContent
            assert obs["provenance"]["data_origin"] == "live"
            page = await session.call_tool("read_result", {"result_id": obs["result_id"]})
            assert not page.isError
            assert page.structuredContent["preview_rows"] == obs["preview_rows"]
            invalid = await session.call_tool("read_result", {"result_id": "not-owned"})
            assert invalid.isError
            return {"ok": True, "tools": len(tools.tools), "status": obs["status"], "rows": obs["total_rows"], "source": "live", "database": env["HARNESS_MONGO_DB"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--ref-date", default="2026-09-12")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(check(args.ref_date))))
