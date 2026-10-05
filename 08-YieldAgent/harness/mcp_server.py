"""Stdio tool host. No planner or LLM calls: the connected host controls reasoning."""
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from contextlib import redirect_stdout

from dotenv import load_dotenv
from mcp import types
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server

from .config import Settings
from .executor import ExecutionContext, ToolExecutor
from .store import HarnessStore, Conflict
from .tools.registry import domain_registry


def tool_definitions(registry):
    return [types.Tool(name=spec.name, description=spec.description, inputSchema=spec.schema.model_json_schema(),
        annotations=types.ToolAnnotations(readOnlyHint=spec.read_only, destructiveHint=False, openWorldHint=True)) for spec in registry.tools.values()]


async def acquire_session(store, principal, session_id, connection_id):
    async with store.session_lock(principal, session_id):
        old = await store.runs.find_one({'principal_id': principal, 'session_id': session_id, 'active': True})
        if old:
            if old.get('execution_kind') != 'mcp' or old.get('lease_until', 0) >= time.time():
                raise Conflict('Session has a live owner')
            fenced = await store.runs.update_one({'_id': old['run_id'], 'epoch': old['epoch'],
                'active': True, 'lease_until': {'$lt': time.time()}}, {'$set': {'status': 'cancelling'}})
            if not fenced.modified_count:
                raise Conflict('Session owner renewed its lease')
            await store.finish(old['run_id'], old['epoch'], 'cancelled',
                '이전 MCP 연결이 만료되었습니다. 저장한 결과는 유지합니다.', 'mcp_connection_expired')
        run = await store.start_run(principal, session_id, connection_id, 'MCP tool session', execution_kind='mcp')
        return await store.acquire(run['run_id'], connection_id)


async def serve():
    load_dotenv()
    settings = Settings.from_env()
    registry = domain_registry()
    store = HarnessStore(settings.mongo_uri, settings.mongo_db)
    await store.setup()
    session_id = os.getenv("HARNESS_MCP_SESSION_ID") or "mcp-" + uuid.uuid4().hex
    connection_id = uuid.uuid4().hex
    run = await acquire_session(store, settings.principal, session_id, connection_id)
    executor = ToolExecutor(registry, ExecutionContext(store, settings, run))
    server = Server("yield-harness", version="2.0.0", instructions="결과는 preview와 전체 원본 result_id를 포함합니다. 전체 계산은 run_python, 추가 행은 read_result를 사용하세요. empty/error/partial을 구분하세요. 출처 없는 인과관계를 단정하지 마세요.")
    gate = asyncio.Semaphore(2)
    inflight = 0
    last_accounted = time.monotonic()

    @server.list_tools()
    async def list_tools():
        return tool_definitions(registry)

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        nonlocal inflight, last_accounted
        async with gate:
            if not inflight:
                current = await store.fence(run["run_id"], run["epoch"])
                last_accounted = time.monotonic()
                executor.context.clock_started = last_accounted
                executor.context.active_at_start = current["usage"]["active_seconds"]
            inflight += 1
            try:
                invocation_id = connection_id + ":" + str(server.request_context.request_id)
                obs = await executor.execute({"name": name, "args": arguments, "id": invocation_id})
                payload = obs.model_dump(mode="json")
                for artifact in payload["artifact_refs"]:
                    artifact["uri"] = "harness://artifact/" + artifact["artifact_id"]
                return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
                    structuredContent=payload, isError=obs.status == "error")
            except asyncio.CancelledError:
                await store.event(run["run_id"], "progress", {"message": "MCP 호출이 취소되었습니다."})
                raise
            except Exception as exc:
                payload = {"status": "error", "error": {"code": type(exc).__name__, "safe_message": "도구 실행이 중단되었습니다. 실행 한도 또는 연결 상태를 확인하세요."}}
                return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))], structuredContent=payload, isError=True)
            finally:
                inflight -= 1
                if not inflight:
                    elapsed = time.monotonic() - last_accounted
                    last_accounted = time.monotonic()
                    await store.renew(run["run_id"], run["epoch"], elapsed)

    @server.list_resources()
    async def list_resources():
        resources = []
        async for result in store.results.find({"principal_id": settings.principal, "session_id": session_id}):
            for ref in result["observation"]["artifact_refs"]:
                resources.append(types.Resource(uri="harness://artifact/" + ref["artifact_id"], name=ref["title"], mimeType=ref["mime"]))
        return resources

    @server.read_resource()
    async def read_resource(uri):
        prefix = "harness://artifact/"
        value = str(uri)
        if not value.startswith(prefix) or "/" in value[len(prefix):]:
            raise ValueError("Unknown resource")
        ref, data = await store.artifact(settings.principal, session_id, value[len(prefix):])
        content = data.decode() if ref["mime"].startswith("text/") else data
        return [ReadResourceContents(content=content, mime_type=ref["mime"])]

    async def heartbeat():
        nonlocal last_accounted
        while True:
            await asyncio.sleep(5)
            current = time.monotonic()
            elapsed = current - last_accounted if inflight else 0
            last_accounted = current
            renewed = await store.renew(run["run_id"], run["epoch"], elapsed)
            if not renewed.modified_count:
                return

    pulse = asyncio.create_task(heartbeat())
    try:
        async with stdio_server() as (read, write):
            # Domain library print() must never corrupt the JSON-RPC stdout channel.
            with redirect_stdout(sys.stderr):
                await server.run(read, write, server.create_initialization_options())
    finally:
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
        await store.finish(run["run_id"], run["epoch"], "completed", "MCP 연결 종료", "host_disconnected")
        store.client.close()


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    asyncio.run(serve())
