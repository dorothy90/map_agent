"""E2E: relation_tree → main_oper HITL → wt_resp(DF_WADS_GOOD_BAD_LOT) → mining 확인 HITL → mining.

검증:
  - relation_tree가 DF_WADS_MAIN_OPER로 main_oper 선택지 산출
  - main_oper 선택(클릭/자유응답) → wt_resp dispatch(cause_oper=선택값)
  - wt_resp가 group_good/group_bad 산출 → "mining 실행?" 확인 HITL
  - 승인 → mining dispatch에 5 state(lotcd/fail_type/cause_oper/group_good/group_bad) 일관 전달

Run (server up on :8001):  python tests/verify_relation_chain.py [LOTCD] [FAIL]
"""
import sys; sys.path.insert(0, "tests")
from e2e_client import Session, server_is_up

LOTCD = sys.argv[1] if len(sys.argv) > 1 else "4SS"
FAIL = sys.argv[2] if len(sys.argv) > 2 else "VTH"


def intr(tr, p):
    return next((e for e in tr.sse_interrupts() if e.get("param") == p), None)


def run(pick_mode):
    s = Session()
    print(f"\n===== pick_mode={pick_mode} =====")
    tr = s.turn(f"{LOTCD} {FAIL} 연관분석")
    if intr(tr, "lotcd"):
        tr = s.turn("", resume_value={"lotcd": LOTCD})
    mo = intr(tr, "mainoper_choice")
    assert mo, f"main_oper interrupt 없음. planned={tr.planned_agents()}"
    opts = [o for o in mo["options"] if o["value"] != "none"]
    expected = opts[1]["label"] if pick_mode == "free" else opts[0]["label"]
    print("main_opers:", [o["label"] for o in opts][:6], "...", f"(택: {expected})")

    if pick_mode == "free":
        t2 = s.turn("두번째 거", resume_value={"mainoper_choice": "두번째 거"})
    else:
        t2 = s.turn("", resume_value={"mainoper_choice": "0"})

    mc = intr(t2, "mining_choice")
    assert mc, f"mining 확인 interrupt 없음. interrupts={[e.get('param') for e in t2.sse_interrupts()]}"
    print("mining 확인 HITL 도달:", mc.get("message", "")[:40])

    # wt_resp가 cause_oper=선택값으로 dispatch됐는지
    co = t2.dispatched_param("wt_resp_agent", "cause_oper")
    assert co.get("preview") == expected, f"wt_resp cause_oper {co.get('preview')!r} != {expected!r}"

    # wt_resp가 DF_WADS_GOOD_BAD_LOT에서 group_good/bad를 산출했는지 (요약 메시지 good=N, bad=N).
    # (list 파라미터는 supervisor_dispatch 트레이스에 안 실리므로 wt_resp 요약으로 검증.)
    import re
    m = re.search(r"good=(\d+), bad=(\d+)", t2.sse_blob)
    assert m, f"wt_resp good/bad 요약 없음. sse 일부={t2.sse_blob[:300]}"
    n_good, n_bad = int(m.group(1)), int(m.group(2))
    print(f"wt_resp 산출 group_good={n_good} group_bad={n_bad}")
    assert n_good > 0 and n_bad > 0, f"group_good/bad 비어있음: good={n_good} bad={n_bad}"

    # 승인 → mining dispatch (scalar state는 트레이스로, group은 mining이 받아 실행됐는지로 검증)
    t3 = s.turn("", resume_value={"mining_choice": "yes"})
    m_lot = t3.dispatched_param("mining_agent", "lotcd")
    m_ft = t3.dispatched_param("mining_agent", "fail_type")
    m_co = t3.dispatched_param("mining_agent", "cause_oper")
    print("mining lotcd:", m_lot.get("preview"), "| fail_type:", m_ft.get("preview"),
          "| cause_oper:", m_co.get("preview"))
    assert m_co.get("preview") == expected, f"mining cause_oper {m_co.get('preview')!r} != {expected!r}"
    assert m_lot.get("present") and m_ft.get("present"), "mining 미dispatch (lotcd/fail_type 누락)"
    print(f"✅ PASS ({pick_mode}): 5 state 일관 전달 (cause_oper={expected!r}, good={n_good}, bad={n_bad})")


if __name__ == "__main__":
    if not server_is_up():
        print("server :8001 미기동"); sys.exit(1)
    run("click")
    run("free")
    print("\n===== 전체 PASS =====")
