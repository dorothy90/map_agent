"""Run explicitly requested wafer-set scenarios against an already running server."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from urllib import request

from harness.tools.wafer_set_register import LIVE_EVAL_TOOL_NAMES

SCENARIOS = {
    "source-discovery": "사용 가능한 데이터 소스와 열, 관계, 지표 정의를 조회해줘.",
    "stored-chain": "저장된 측정 집합을 집계하고 비교한 뒤 표 보기로 보여줘.",
    "map-chain": "저장된 맵 측정의 좌표 지표를 누적하고 heatmap으로 보여줘.",
}


def call(method, url, body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = request.Request(url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data is not None else {})
    with request.urlopen(req, timeout=30) as response:
        return json.loads(response.read())


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    base = args.base_url.rstrip("/")
    started = call("POST", base + "/runs", {"session_id": args.session_id,
        "request_id": f"wafer-set-{time.time_ns()}", "query": SCENARIOS[args.scenario]})
    run_id = started["run_id"]
    deadline = time.monotonic() + 300
    state = started
    while state.get("status") in ("queued", "running", "waiting") and time.monotonic() < deadline:
        time.sleep(1)
        state = call("GET", base + f"/runs/{run_id}")
    if state.get("status") in ("queued", "running", "waiting"):
        raise TimeoutError("Live evaluation did not finish within 300 seconds")
    calls = {item.get("tool_name") for item in state.get("observations", [])}
    forbidden = calls - LIVE_EVAL_TOOL_NAMES
    result = {"scenario": args.scenario, "run_id": run_id, "status": state.get("status"),
        "observed_tools": sorted(name for name in calls if name),
        "live_allowlist": sorted(LIVE_EVAL_TOOL_NAMES),
        "forbidden_wafer_set_tools": sorted(forbidden & {"export_report", "run_python"}),
        "state": state}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    destination = args.output_dir / f"{args.scenario}-{run_id}.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "state"}, ensure_ascii=False))
    if result["forbidden_wafer_set_tools"] or state.get("status") != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
