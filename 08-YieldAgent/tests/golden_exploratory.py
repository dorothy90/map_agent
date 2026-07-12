"""Golden eval — EXPLORATORY multi-agent queries (recall-scored).

The existing `test_e2e_regression.py` already pins DETERMINISTIC routing/slot/HITL
behavior (binary pass/fail). This module adds the missing half for the model-driven
supervisor refactor: OPEN-ENDED queries where a good answer fans out across several
agents and the *path is discovered as it runs* ("4SS 수율 왜 떨어졌어?" → map → 불량이력
→ mining …). There is no single right path, so we score by RECALL, not equality:

  agent_recall    = |expected_agents ∩ planned_agents| / |expected_agents|
  artifact_recall = |expected_artifacts present in answer| / |expected_artifacts|

The metric is the one chosen in the refactor interview (정답 경로/아티팩트 recall) —
deterministic, no LLM-judge, so the gate is cheap and reproducible.

Workflow (strangler step 1):
    uvicorn agent_server:app --port 8001            # live server (LLM+DB)
    python tests/golden_exploratory.py --freeze     # freeze CURRENT system as baseline
    # ... later, on the deepagents-based supervisor:
    python tests/golden_exploratory.py              # run + diff vs baseline (go/no-go gate)

NOTE(user-owned): the EXPLORATORY scenarios below are SEED examples derived from the
domain — the spec assigns scenario authorship to the domain expert. Review/replace the
`expect` sets (which agents *should* fire, which artifacts *must* appear) before trusting
the baseline. Marked TODO(user) inline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from e2e_client import Session, TurnResult, server_is_up

BASELINE_PATH = Path(__file__).resolve().parent / "baseline_exploratory.json"

# ─────────────────────────────────────────────────────────────
# Exploratory scenarios. Each: {id, turns:[...], expect:{agents:[...], artifacts:[...]}}
#   agents    — the agent set a *good* multi-agent answer should reach (recall target)
#   artifacts — substrings that must appear in the answer (key findings/artifact markers)
# TODO(user): these are SEED guesses. Replace expected sets with domain truth.
# ─────────────────────────────────────────────────────────────
EXPLORATORY: list[dict] = [
    {
        "id": "yield_drop_root_cause",
        "turns": ["4SS 수율이 왜 떨어졌는지 끝까지 분석해줘"],
        "expect": {
            "agents": ["yield_agent", "wads_agent", "map_agent", "fail_history_agent"],
            "artifacts": ["수율", "검출", "맵", "불량"],
        },
    },
    {
        "id": "detect_lots_deep_dive",
        "turns": ["최근 1주일 4SS 검출 lot들 원인 깊게 파봐"],
        "expect": {
            "agents": ["wads_agent", "map_agent", "mining_agent", "relation_tree_agent"],
            "artifacts": ["검출", "원인", "맵"],
        },
    },
    {
        "id": "followup_why_low",
        "turns": [
            "4SS 최근 3주 수율 보여줘",
            "왜 이렇게 낮은지 관련된 거 다 분석해줘",
        ],
        "expect": {
            "agents": ["yield_agent", "wads_agent", "map_agent", "fail_history_agent"],
            "artifacts": ["수율", "검출", "불량"],
        },
    },
]


def _run_scenario(case: dict) -> TurnResult:
    session = Session()
    results = [session.turn(q) for q in case["turns"]]
    last = results[-1]
    last.turns = results
    return last


def _score(case: dict, r: TurnResult) -> dict:
    """Recall across all turns of the scenario (an emergent path may fan out over turns)."""
    planned: set[str] = set()
    blob = ""
    for t in (r.turns or [r]):
        planned.update(a for a in t.planned_agents() if a)
        blob += t.sse_blob

    exp_agents = case["expect"]["agents"]
    exp_artifacts = case["expect"]["artifacts"]
    hit_agents = [a for a in exp_agents if a in planned]
    hit_artifacts = [s for s in exp_artifacts if s in blob]

    return {
        "id": case["id"],
        "agent_recall": round(len(hit_agents) / len(exp_agents), 3) if exp_agents else None,
        "artifact_recall": round(len(hit_artifacts) / len(exp_artifacts), 3) if exp_artifacts else None,
        "planned_agents": sorted(planned),
        "expected_agents": exp_agents,
        "missing_agents": [a for a in exp_agents if a not in planned],
        "missing_artifacts": [s for s in exp_artifacts if s not in blob],
    }


def _aggregate(scores: list[dict]) -> dict:
    ar = [s["agent_recall"] for s in scores if s["agent_recall"] is not None]
    fr = [s["artifact_recall"] for s in scores if s["artifact_recall"] is not None]
    return {
        "mean_agent_recall": round(sum(ar) / len(ar), 3) if ar else None,
        "mean_artifact_recall": round(sum(fr) / len(fr), 3) if fr else None,
        "n": len(scores),
    }


def _run_all() -> dict:
    scores = [_score(c, _run_scenario(c)) for c in EXPLORATORY]
    return {"scores": {s["id"]: s for s in scores}, "aggregate": _aggregate(scores)}


def main() -> int:
    if not server_is_up():
        print("SKIP: agent server not up — start `uvicorn agent_server:app --port 8001`")
        return 2

    freeze = "--freeze" in sys.argv
    result = _run_all()

    for sid, s in result["scores"].items():
        print(f"{sid}: agent_recall={s['agent_recall']} artifact_recall={s['artifact_recall']}")
        if s["missing_agents"]:
            print(f"    missing agents:    {s['missing_agents']}")
        if s["missing_artifacts"]:
            print(f"    missing artifacts: {s['missing_artifacts']}")
    print(f"\naggregate: {result['aggregate']}")

    if freeze:
        BASELINE_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nbaseline frozen → {BASELINE_PATH.name}")
        return 0

    if not BASELINE_PATH.exists():
        print(f"\nno baseline ({BASELINE_PATH.name}); run with --freeze first")
        return 2

    base = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    regressions = []
    for sid, s in result["scores"].items():
        b = base["scores"].get(sid)
        if not b:
            continue
        for k in ("agent_recall", "artifact_recall"):
            if s[k] is not None and b[k] is not None and s[k] < b[k] - 1e-9:
                regressions.append(f"{sid}.{k}: {b[k]} → {s[k]}")
    if regressions:
        print("\nREGRESSIONS vs baseline:")
        for x in regressions:
            print(f"  - {x}")
        return 1
    print("\nno regressions vs baseline (go-gate: improvements OK)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
