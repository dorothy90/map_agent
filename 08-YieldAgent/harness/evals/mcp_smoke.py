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
            assert "query_yield" in {tool.name for tool in tools.tools}
            result = await session.call_tool("query_yield", {"lotcd": "4SS", "ref_date": ref_date, "periods": 4})
            assert not result.isError, result.content
            obs = result.structuredContent
            assert obs["provenance"]["data_origin"] == "live"
            page = await session.call_tool("read_result", {"result_id": obs["result_id"]})
            assert not page.isError
            assert page.structuredContent["preview_rows"] == obs["preview_rows"]
            resource = await session.read_resource(obs["artifact_refs"][0]["uri"])
            html = resource.contents[0].text
            assert resource.contents[0].mimeType == "text/html"
            assert obs["scope"]["value_units"]["GMS"] == "percent"
            from html.parser import HTMLParser
            class TableReader(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.rows, self.row, self.cell = [], [], None
                def handle_starttag(self, tag, attrs):
                    if tag == "tr":
                        self.row = []
                    elif tag == "td":
                        self.cell = ""
                def handle_data(self, value):
                    if self.cell is not None:
                        self.cell += value
                def handle_endtag(self, tag):
                    if tag == "td":
                        self.row.append(self.cell.strip())
                        self.cell = None
                    elif tag == "tr" and self.row:
                        self.rows.append(self.row)
            parsed = TableReader()
            parsed.feed(html)
            # Compare cells from the actual MCP resource against original DB
            # values. This checks preservation, not inferred physical units.
            from common import PARA_COLUMNS, PT1C_COLUMNS, GMS_COLUMNS
            keys = [*PARA_COLUMNS, *("pt1c_" + c for c in PT1C_COLUMNS), *("gms_" + c for c in GMS_COLUMNS)]
            for source in obs["preview_rows"]:
                rendered = next(row for row in parsed.rows if row[0] == source["week"])
                expected = ["-" if source.get(key) in (None, "-", "") else f"{float(source[key]):.2f}" for key in keys]
                assert rendered[3:] == expected, source["week"]
            invalid = await session.call_tool("read_result", {"result_id": "not-owned"})
            assert invalid.isError
            return {"ok": True, "tools": len(tools.tools), "status": obs["status"], "rows": obs["total_rows"], "source": "live", "table_values_match_source": True, "database": env["HARNESS_MONGO_DB"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--ref-date", default="2026-09-12")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(check(args.ref_date))))
