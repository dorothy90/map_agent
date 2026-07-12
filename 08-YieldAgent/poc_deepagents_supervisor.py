"""PoC — deepagents model-driven supervisor over EXISTING agent nodes (strangler step 2).

Goal: prove the model-driven topology fans out across agents on OPEN-ENDED queries
where the front-loaded plan-and-execute planner routes to a single agent (baseline
agent_recall = 0.25, see baseline_exploratory.json).

Scope (per refactor spec): map + fail_history only. We do NOT rewrite them — each is
wrapped as a `task`-callable tool that invokes the real LangGraph node. The deepagents
supervisor (same OpenRouter model via common.get_llm) decides, per query, which to call
and in what order — emergent, not pre-planned. The builtin FilesystemMiddleware gives the
virtual FS; large HTML artifacts are written there so the supervisor context stays small.

Run (server NOT required — this builds the graph in-process; it does need DB/LLM env):
    python poc_deepagents_supervisor.py            # run exploratory scenarios + score vs baseline
    python poc_deepagents_supervisor.py --once "4SS 수율 왜 떨어졌어 분석해줘"
"""

from __future__ import annotations

import os

# Suppress LangSmith ingest (monthly-limit 429 spam) — PoC measures locally, not in LS.
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING"] = "false"

import json
import re
import sys
from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from common import get_llm

import map_agent
import fail_history_agent

# which wrapped agents the supervisor actually invoked this run (reset per scenario)
CALLED: list[str] = []


def _pick_artifacts(out: dict) -> list:
    """Node return dicts use per-agent keys (yield_artifacts, mining_artifacts, …).
    Grab whichever *_artifacts list is present — avoids hardcoding each key."""
    for k, v in (out or {}).items():
        if k.endswith("_artifacts") and isinstance(v, list):
            return v
    return []


def _summarize_artifacts(arts: list, label: str) -> str:
    """Keep the supervisor context small: report count + short text, not the HTML blob."""
    if not arts:
        return f"{label}: 결과 없음(빈 artifact)"
    parts = []
    for a in arts[:3]:
        if isinstance(a, dict):
            txt = a.get("summary") or a.get("title") or a.get("type") or ""
            parts.append(str(txt)[:200])
        else:
            parts.append(str(a)[:200])
    return f"{label}: artifact {len(arts)}개 — " + " | ".join(p for p in parts if p)


_LOT_RE = re.compile(r"\b4SS[A-Z0-9]{4}\b")


@tool
def run_wads_agent(lotcd: str, fail_type: str = "") -> str:
    """WADS 열화 검출 리포트를 조회해 '검출된 lot'들을 찾는다. 특정 lot을 아직 모를 때
    가장 먼저 호출 — 여기서 얻은 lot id를 run_map_agent에 넘긴다.
    lotcd: 제품코드(예: "4SS"). fail_type: 불량 유형(선택)."""
    CALLED.append("wads_agent")
    import wads_agent
    state = {
        "lotcd": str(lotcd or ""),
        "fail_type": str(fail_type or ""),
        "messages": [HumanMessage(content=f"{lotcd} 최근 검출 lot")],
        "current_task_goal": f"{lotcd} WADS 검출 lot 조회",
    }
    try:
        out = wads_agent.wads_agent_node(state, {})
    except Exception as e:
        return f"wads_agent 실행 오류: {e}"
    arts = out.get("wads_artifacts") or []
    lots = sorted(set(_LOT_RE.findall(json.dumps(arts, ensure_ascii=False, default=str))))
    if lots:
        return f"WADS 검출 lot {len(lots)}개: {','.join(lots[:20])}"
    return _summarize_artifacts(arts, "WADS 검출")


@tool
def run_map_agent(lot_ids: str, map_oper: str) -> str:
    """웨이퍼 맵(wafer map)을 시각화한다. 불량 패턴을 눈으로 확인할 때 호출.
    lot_ids: 쉼표로 구분된 lot id (예: "4SS2DPD,4SSXCEW"). map_oper: 공정 단계(예: "PT1H")."""
    CALLED.append("map_agent")
    state = {
        "lot_ids": [s.strip() for s in str(lot_ids).split(",") if s.strip()],
        "map_oper": str(map_oper or ""),
        "map_type": "binmap",
        "messages": [],
    }
    try:
        out = map_agent.map_agent_node(state, {})
    except Exception as e:
        return f"map_agent 실행 오류: {e}"
    return _summarize_artifacts(out.get("map_artifacts") or [], "맵(wafer map)")


@tool
def run_fail_history_agent(lotcd: str, fail_type: str = "", cause_oper: str = "") -> str:
    """불량 이력(fail history)을 RAG로 조사한다. 과거 유사 불량의 원인/조치를 찾을 때 호출.
    lotcd: 제품코드(예: "4SS"). fail_type: 불량 유형(선택). cause_oper: 원인 공정(선택)."""
    CALLED.append("fail_history_agent")
    state = {
        "lotcd": str(lotcd or ""),
        "fail_type": str(fail_type or ""),
        "cause_oper": str(cause_oper or ""),
        "messages": [HumanMessage(content=f"{lotcd} 불량이력 조사")],
        "current_task_goal": f"{lotcd} 불량이력 원인 분석",
    }
    try:
        out = fail_history_agent.fail_history_agent_node(state, {})
    except Exception as e:
        return f"fail_history_agent 실행 오류: {e}"
    return _summarize_artifacts(out.get("fail_history_artifacts") or [], "불량이력")


@tool
def run_yield_agent(lotcd: str) -> str:
    """수율(yield) 추이를 조회한다. 수율이 실제로 떨어졌는지/언제부터인지 먼저 확인할 때 호출.
    lotcd: 제품코드(예: "4SS")."""
    CALLED.append("yield_agent")
    import yield_query_agent
    state = {"lotcd": str(lotcd or ""), "messages": [HumanMessage(content=f"{lotcd} 수율")]}
    try:
        out = yield_query_agent.yield_agent_node(state, {})
    except Exception as e:
        return f"yield_agent 실행 오류: {e}"
    return _summarize_artifacts(_pick_artifacts(out), "수율")


@tool
def run_mining_agent(lotcd: str, fail_type: str = "") -> str:
    """불량 인자(parameter) 마이닝 — good/bad 그룹을 비교해 원인 인자를 통계적으로 찾는다.
    불량 패턴은 봤는데 '무엇이 원인 인자인지' 더 파야 할 때 호출. lotcd, fail_type."""
    CALLED.append("mining_agent")
    import mining_agent
    state = {
        "lotcd": str(lotcd or ""), "fail_type": str(fail_type or ""),
        "messages": [HumanMessage(content=f"{lotcd} 불량 인자 마이닝")],
        "current_task_goal": f"{lotcd} 원인 인자 마이닝",
    }
    try:
        out = mining_agent.mining_agent_node(state, {})
    except Exception as e:
        return f"mining_agent 실행 오류: {e}"
    return _summarize_artifacts(_pick_artifacts(out), "원인 인자 마이닝")


@tool
def run_relation_tree_agent(lotcd: str, fail_type: str = "", cause_oper: str = "") -> str:
    """연계 분석(relation tree) — 불량과 공정/설비 간 연계 구조를 트리로 분석한다.
    원인 공정·설비의 연결고리를 추적할 때 호출. lotcd, fail_type, cause_oper(선택)."""
    CALLED.append("relation_tree_agent")
    import relation_tree_agent
    state = {
        "lotcd": str(lotcd or ""), "fail_type": str(fail_type or ""),
        "cause_oper": str(cause_oper or ""),
        "messages": [HumanMessage(content=f"{lotcd} 연계 분석")],
        "current_task_goal": f"{lotcd} 연계 분석",
    }
    try:
        out = relation_tree_agent.relation_tree_agent_node(state, {})
    except Exception as e:
        return f"relation_tree_agent 실행 오류: {e}"
    return _summarize_artifacts(_pick_artifacts(out), "연계 분석")


SYSTEM_PROMPT = (
    "너는 반도체 수율 분석 supervisor다. 사용자의 '열린' 원인분석 질의에 답하기 위해, "
    "필요한 전문 도구를 스스로 판단해 호출하고 결과를 종합한다. 다음 스텝은 이전 도구의 "
    "결과를 보고 결정해라(사전 계획 금지). 사용자가 특정 lot을 주지 않고 제품코드(예: 4SS)만 "
    "준 수율 하락/불량 원인 질의라면, 사용자에게 되묻지 말고 스스로 조사해라. 쓸 수 있는 도구: "
    "run_yield_agent(수율 추이 확인), run_wads_agent(검출된 lot 확보), "
    "run_map_agent(lot의 불량 패턴 시각화), run_fail_history_agent(과거 유사 불량 원인/조치), "
    "run_mining_agent(good/bad 비교로 원인 인자 마이닝), run_relation_tree_agent(공정/설비 연계 추적). "
    "전형적 흐름: 수율로 하락 확인 → wads로 검출 lot 확보 → 그 lot을 map으로 패턴 확인 → "
    "fail_history/mining/relation으로 원인을 교차 심화. 다음 스텝은 직전 결과를 보고 정해라(사전계획 금지). "
    "한 도구로 부족하면 다른 도구도 호출해라. 사용자가 '왜/끝까지/다/관련된 거 전부' 같이 "
    "철저한 원인분석을 요구하면, 도구 하나만 쓰고 멈추지 마라 — 최소한 검출(wads)→패턴(map)→"
    "원인 심화(불량이력/마이닝/연계) 중 여러 각도로 교차확인할 때까지 계속 조사해라. "
    "큰 결과물(HTML 등)은 파일로 저장하고 본문엔 핵심 요약만 남겨라. 마지막에 발견을 한국어로 "
    "종합 정리한다."
)


def build_supervisor():
    from deepagents import create_deep_agent

    return create_deep_agent(
        model=get_llm(),
        tools=[
            run_yield_agent, run_wads_agent, run_map_agent,
            run_fail_history_agent, run_mining_agent, run_relation_tree_agent,
        ],
        system_prompt=SYSTEM_PROMPT,
    )


def run_query(agent, query: str, history: list | None = None) -> dict:
    """Run one user turn. Pass the prior turn's `messages` as `history` to thread
    multi-turn context (no checkpointer → we replay the accumulated history each turn)."""
    CALLED.clear()
    msgs = list(history or []) + [{"role": "user", "content": query}]
    result = agent.invoke({"messages": msgs})
    out_msgs = result.get("messages", [])
    final = ""
    for m in reversed(out_msgs):
        content = getattr(m, "content", "")
        if content and getattr(m, "type", "") in ("ai", "assistant"):
            final = content if isinstance(content, str) else str(content)
            break
    return {"called": list(CALLED), "final": final, "messages": out_msgs}


# ── eval: reuse the exploratory scenarios + baseline from the golden harness ──
def _score_vs_baseline():
    sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))
    import golden_exploratory as g

    base = json.loads(g.BASELINE_PATH.read_text(encoding="utf-8"))
    agent = build_supervisor()
    runs = int(os.getenv("POC_RUNS", "2"))  # model-driven supervisor is non-deterministic → average
    results_path = Path(__file__).resolve().parent / "poc_gate_results.json"

    print(f"=== PoC deepagents supervisor vs baseline (exploratory recall, {runs} runs avg) ===\n", flush=True)
    means, persisted = [], {}
    for case in g.EXPLORATORY:
        exp = case["expect"]["agents"]
        recalls = []
        for i in range(runs):
            called: set = set()
            history: list = []
            for turn in case["turns"]:
                r = run_query(agent, turn, history)
                called.update(r["called"])
                history = r["messages"]  # thread context into the next turn
            recalls.append(len([a for a in exp if a in called]) / len(exp) if exp else 0.0)
            # persist after EVERY run so a kill keeps partial data
            persisted[case["id"]] = {"runs_done": i + 1, "recalls": recalls,
                                     "mean": round(sum(recalls) / len(recalls), 3)}
            results_path.write_text(json.dumps(persisted, ensure_ascii=False, indent=2), encoding="utf-8")
        mean = round(sum(recalls) / len(recalls), 3)
        means.append(mean)
        b_recall = base["scores"].get(case["id"], {}).get("agent_recall")
        mark = "▲" if (b_recall is not None and mean > b_recall) else "="
        print(f"{mark} {case['id']}: mean={mean} (min {round(min(recalls),2)}–max {round(max(recalls),2)}) "
              f"vs baseline {b_recall}", flush=True)
    print(f"\nMEAN agent_recall across scenarios: {round(sum(means)/len(means),3)} "
          f"(baseline 0.25). Per-scenario min–max spread = the determinism risk, quantified.", flush=True)
    print("\n(PoC scope = yield+wads+map+fail_history+mining+relation_tree — full roster "
          "for these scenarios, so ceiling is 1.0. Proof = emergent fan-out to MULTIPLE "
          "agents on one open-ended query vs baseline's single agent. Some backends may be "
          "down locally — recall counts the CALL/route, consistent with the baseline metric.)")


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--once":
        agent = build_supervisor()
        r = run_query(agent, sys.argv[2])
        print("called:", r["called"])
        print("final:\n", r["final"][:2000])
        return 0
    _score_vs_baseline()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
