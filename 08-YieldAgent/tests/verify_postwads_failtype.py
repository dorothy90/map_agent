"""Ad-hoc E2E verification for the new WADS fail_type 2-step HITL.

Flow under test:
  wads 검출 → (1) fail_type 선택 interrupt → (2) 분석종류 선택 interrupt → 1개 report로만 후속.

Checks:
  A. 1차 interrupt가 fail_type 선택지(param=postwads_failtype, label='parameter @ map_oper')인지.
  B. 버튼 클릭(value="0") resume → 2차 interrupt(분석종류)로 진행 (replay-safe, 결정적 경로).
  C. 자유응답 resume(예: '두번째 거') → 2차 interrupt 진행, 분석종류 선택 후 결과가
     1차에서 고른 fail_type 1개로 국한되는지(전 report fan-out 사라짐 + replay 비일관성 없음).

Run (server must be up on :8001):
    python tests/verify_postwads_failtype.py
"""
from __future__ import annotations

import sys

from e2e_client import Session, server_is_up

# 열화 리포트 모드라야 per-report breakdown(extensions.wads_agent.reports)이 생성된다.
# 단순 "검출 lot 리스트"는 report 없음 → fail_type 선택 단계 생략(no-reports 가드).
WADS_QUERY = "4SS 열화 리포트 보여줘"


def _failtype_interrupt(tr):
    """가장 최근 fail_type 선택 interrupt(있으면) 반환."""
    for e in tr.sse_interrupts():
        if e.get("param") == "postwads_failtype":
            return e
    return None


def _analysis_interrupt(tr):
    for e in tr.sse_interrupts():
        if e.get("param") == "postwads_choice":
            return e
    return None


def run_click_path():
    print("\n=== B. 버튼 클릭 경로 (결정적) ===")
    s = Session()
    tr = s.turn(WADS_QUERY)
    ft = _failtype_interrupt(tr)
    assert ft, f"1차 fail_type interrupt 없음. interrupts={tr.sse_interrupts()}"
    opts = ft.get("options") or []
    print("A. fail_type 선택지:", [(o.get("label"), o.get("value")) for o in opts])
    report_opts = [o for o in opts if o.get("value") != "none"]
    assert report_opts, "report 선택지가 없음"
    assert any("@" in (o.get("label") or "") for o in report_opts), \
        "라벨이 'parameter @ map_oper' 형식이 아님"

    # 버튼 클릭 = value="0" (첫 report)
    expected_param = (report_opts[0].get("label") or "").split(" @ ")[0]
    tr2 = s.turn("", resume_value={"postwads_failtype": "0"})
    an = _analysis_interrupt(tr2)
    assert an, f"2차 분석종류 interrupt 없음. interrupts={tr2.sse_interrupts()}"
    print("B. 분석종류 interrupt 도달:", [o.get("value") for o in (an.get("options") or [])])

    # 분석종류 = relation_tree → dispatch된 fail_type이 선택 report 1개 parameter인지 검증
    # (전 report fan-out이면 5개 parameter가 콤마조인됨)
    tr3 = s.turn("", resume_value={"postwads_choice": "relation_tree_agent"})
    _assert_single_failtype(tr3, expected_param, "B")
    print(f"✅ B PASS: 클릭 경로 2-step + 단일 report 스코핑(fail_type={expected_param!r})")


def run_freetext_path():
    print("\n=== C. 자유응답 경로 (replay-safety) ===")
    s = Session()
    tr = s.turn(WADS_QUERY)
    ft = _failtype_interrupt(tr)
    assert ft, f"1차 fail_type interrupt 없음. interrupts={tr.sse_interrupts()}"
    opts = [o for o in (ft.get("options") or []) if o.get("value") != "none"]
    print("C. fail_type 선택지:", [(o.get("label"), o.get("value")) for o in opts])
    if len(opts) < 2:
        print("C. report가 2개 미만 → '두번째 거' 의미 약함. 1개 선택으로 대체 검증.")
        pick_text = opts[0].get("label")
        expected_label = opts[0].get("label")
    else:
        pick_text = "두번째 거"
        expected_label = opts[1].get("label")

    tr2 = s.turn(pick_text, resume_value={"postwads_failtype": pick_text})
    an = _analysis_interrupt(tr2)
    assert an, f"2차 분석종류 interrupt 없음. interrupts={tr2.sse_interrupts()}"
    print("C. 분석종류 interrupt 도달 (자유응답이 fail_type로 해석됨)")

    expected_param = (expected_label or "").split(" @ ")[0]
    tr3 = s.turn("", resume_value={"postwads_choice": "relation_tree_agent"})
    print("C. 기대 fail_type(parameter):", expected_param)
    # replay로 1차 선택이 뒤집혔다면 fail_type이 다른 report parameter로 나온다.
    _assert_single_failtype(tr3, expected_param, "C")
    print(f"✅ C PASS: 자유응답 2-step + 단일 report 스코핑(fail_type={expected_param!r}, replay-safe)")


def _assert_single_failtype(tr, expected_param: str, tag: str):
    """relation_tree_agent에 dispatch된 fail_type이 선택 report 1개 parameter인지 검증."""
    meta = tr.dispatched_param("relation_tree_agent", "fail_type")
    print(f"{tag}. dispatched fail_type meta:", meta)
    assert meta.get("present"), f"fail_type 미전달: {meta}"
    preview = str(meta.get("preview") or "")
    assert "," not in preview, f"fail_type에 콤마(전 report fan-out 의심): {preview!r}"
    assert preview == expected_param, \
        f"fail_type({preview!r}) != 선택 parameter({expected_param!r}) — replay/스코핑 오류"


def main():
    if not server_is_up():
        print("server :8001 미기동")
        sys.exit(1)
    run_click_path()
    run_freetext_path()
    print("\n=== 전체 완료 ===")


if __name__ == "__main__":
    main()
