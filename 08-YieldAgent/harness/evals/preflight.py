import argparse
import asyncio
import json
import os
import subprocess
from dotenv import load_dotenv

from harness.config import Settings
from harness.model import build_model
from harness.store import HarnessStore


async def check():
    load_dotenv()
    results = {}
    settings = Settings.from_env()
    store = HarnessStore(settings.mongo_uri, settings.mongo_db)
    try:
        await store.client.admin.command("ping")
        results["mongo"] = {"ok": True}
    except Exception as exc:
        results["mongo"] = {"ok": False, "error_type": type(exc).__name__}
    finally:
        store.client.close()
    try:
        model = build_model(settings).bind_tools([{"type": "function", "function": {"name": "check_connection", "description": "연결 확인", "parameters": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}}}], tool_choice="check_connection")
        result = await model.ainvoke("check_connection 도구에 value=ok를 넣어 연결을 확인하세요.")
        results["llm_tool_call"] = {"ok": any(t["name"] == "check_connection" for t in result.tool_calls), "model": settings.model}
    except Exception as exc:
        results["llm_tool_call"] = {"ok": False, "error_type": type(exc).__name__, "status": getattr(exc, "status_code", None)}
    try:
        def oracle():
            import oracledb
            with oracledb.connect(user=os.getenv("ORACLE_USER"), password=os.getenv("ORACLE_PASSWORD"), dsn=os.getenv("ORACLE_DSN"), tcp_connect_timeout=5) as connection:
                connection.call_timeout = 5000
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1 FROM DUAL")
                    return cursor.fetchone()[0] == 1
        results["oracle"] = {"ok": await asyncio.to_thread(oracle)}
    except Exception as exc:
        results["oracle"] = {"ok": False, "error_type": type(exc).__name__}
    try:
        p = await asyncio.to_thread(subprocess.run, ["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=10)
        results["docker"] = {"ok": p.returncode == 0}
    except Exception as exc:
        results["docker"] = {"ok": False, "error_type": type(exc).__name__}
    try:
        def search():
            from fail_history_tools import _get_opensearch_client, _OPENSEARCH_INDEX
            client = _get_opensearch_client()
            return client.indices.exists(index=_OPENSEARCH_INDEX)
        results['search'] = {'ok': bool(await asyncio.to_thread(search))}
    except Exception as exc:
        results['search'] = {'ok': False, 'error_type': type(exc).__name__}
    from harness.tools.analysis_tools import mining_availability
    mining = mining_availability()
    results['mining'] = {'ok': mining['available'], 'optional': True, 'reason': mining.get('reason', '')}
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-live", action="store_true")
    parser.parse_args()
    results = asyncio.run(check())
    print(json.dumps(results, ensure_ascii=False, indent=2))
    raise SystemExit(0 if all(r["ok"] for r in results.values() if not r.get("optional")) else 1)


if __name__ == "__main__":
    main()
