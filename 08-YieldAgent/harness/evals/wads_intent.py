"""Inspect live-model tool selection with synthetic conversation text only."""
import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langsmith import tracing_context

from harness.config import Settings
from harness.instructions import load_instructions
from harness.model import build_model
from harness.nodes import Nodes
from harness.tools.registry import domain_registry


async def evaluate():
    load_dotenv()
    settings = Settings.from_env()
    registry = domain_registry()
    model = build_model(settings)
    cases = []
    for question in ("8월 열화리포트 통계좀 내줄래?", "열화리포트 보여달라고"):
        calls, tokens = [], 0

        async def check():
            return {"usage": {"tokens": tokens, "models": len(calls), "tools": 0}}

        async def model_call(bound, messages, *, final=False):
            nonlocal tokens
            with tracing_context(enabled=False):
                response = await bound.ainvoke(messages)
            tokens += (response.usage_metadata or {}).get("total_tokens", 0)
            calls.append(response.tool_calls)
            return response

        async def event(*args, **kwargs):
            pass

        context = SimpleNamespace(settings=settings, check=check, model_call=model_call, store=SimpleNamespace(event=event))
        nodes = Nodes(model, registry, context, load_instructions())
        state = {"run_id": "synthetic-intent", "goal": {"original_request": question}, "observations": [], "messages": [
            HumanMessage(content="가상 제품 DEMO의 월별 수율을 조회해줘."),
            AIMessage(content="가상 제품 DEMO의 월별 수율표를 조회했습니다. 실제 생산 자료가 아닌 가상 대화입니다."),
            HumanMessage(content=question),
        ]}
        selected = []
        for _ in range(5):
            state.update(await nodes.think(state))
            if state.get("status") in ("partial", "failed"):
                break
            while state.get("pending"):
                call = state["pending"][0]
                if call["name"] not in ("load_tools", "update_worklog"):
                    selected.append(call)
                    break
                state.update(await nodes.execute(state))
            if selected:
                break
        case = {"query": question, "selected": selected, "goal": state["goal"], "calls": calls, "tokens": tokens}
        cases.append(case)
        print(json.dumps(case, ensure_ascii=False), flush=True)
    return {"model": settings.model, "data_origin": "fixture", "domain_tools_executed": False, "cases": cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(evaluate())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if all(c["selected"] and c["selected"][0]["name"] in ("query_wads", "get_wads_report")
        and c["selected"][0]["args"].get("lotcd") == "DEMO" for c in report["cases"]) else 1)
