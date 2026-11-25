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


async def check():
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(root), "HARNESS_MONGO_DB": "harness_mcp_eval_" + uuid.uuid4().hex}
    params = StdioServerParameters(command=sys.executable, args=["-m", "harness.mcp_server"], cwd=str(root.parent), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "read_result", "query_db", "list_tables",
                "load_wafer_maps", "render_yield_table",
            }
            result = await session.call_tool("list_tables", {})
            assert not result.isError, result.content
            obs = result.structuredContent
            assert obs["provenance"]["data_origin"] == "live"
            page = await session.call_tool("read_result", {"result_id": obs["result_id"]})
            assert not page.isError
            assert page.structuredContent["preview_rows"] == obs["preview_rows"][:50]
            invalid = await session.call_tool("read_result", {"result_id": "not-owned"})
            assert invalid.isError
            return {"ok": True, "tools": len(tools.tools), "status": obs["status"], "rows": obs["total_rows"], "source": "live", "database": env["HARNESS_MONGO_DB"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-live", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(check())))
