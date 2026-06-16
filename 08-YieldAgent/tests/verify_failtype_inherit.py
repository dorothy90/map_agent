"""E2E: postwads fail_type/lotcd 선택 → 다음 turn 문맥 상속(planner 판단) 검증.

  - wads(제품 없는 쿼리) → postwads_failtype 선택 → postwads_choice=map_agent
  - 같은 맥락 후속 "연관공정도 보여줘" → planner가 lotcd+fail_type 이어받음,
    relation_tree가 lotcd 재질문 없이 실행 (lotcd projection 버그 회귀 가드)
  - 독립 요청(다른 lot 수율) → 상속 안 함 (stale 파라미터 leak X)

WADS 시드 데이터는 매 실행 랜덤이므로 파라미터를 하드코딩하지 않고 선택지에서 동적으로 읽는다.

Run (server up on :8001):  python tests/verify_failtype_inherit.py
"""
import sys; sys.path.insert(0, "tests")
from e2e_client import Session, server_is_up

assert server_is_up(), "서버(:8001) 미기동"


def intr(tr, p):
    return next((e for e in tr.sse_interrupts() if e.get("param") == p), None)


def pick_real_param(options):
    """'PARAM @ OPER' 라벨에서 실제 파라미터가 있는 첫 선택지의 (value, param) 반환."""
    for o in options:
        if o.get("value") == "none":
            continue
        param = str(o.get("label") or "").split(" @ ")[0].strip()
        if param and param != "(unknown)":
            return o.get("value"), param
    raise AssertionError(f"실제 파라미터 선택지 없음: {options}")


# ── 1) 이어받음 경로 (제품 없는 쿼리 → lotcd 재질문 회귀 가드) ──────────
s = Session()
t1 = s.turn("어제 열화리포트 보여줘")
ft = intr(t1, "postwads_failtype")
assert ft, f"postwads_failtype interrupt 없음. interrupts={[e.get('param') for e in t1.sse_interrupts()]}"
idx, param = pick_real_param(ft.get("options") or [])
print(f"1) postwads_failtype 선택: idx={idx} param={param}")

t2 = s.turn("", resume_value={"postwads_failtype": idx})
assert intr(t2, "postwads_choice"), "postwads_choice interrupt 없음"
print("2) postwads_choice HITL 도달")

s.turn("", resume_value={"postwads_choice": "map_agent"})
print("3) map 실행 완료")

t4 = s.turn("연관공정도 보여줘")
agents = t4.planned_agents()
rt = t4.slots_for("relation_tree_agent")
lotcd_reask = intr(t4, "lotcd")
ft_reask = intr(t4, "fail_type")
print(f"4) planned={agents} slots={rt} lotcd재질문={'O' if lotcd_reask else 'X'} fail_type재질문={'O' if ft_reask else 'X'}")
assert "relation_tree_agent" in agents, f"relation_tree 미계획: {agents}"
# 핵심(이번 버그): planner가 채운 lotcd가 살아남아 lotcd 재질문이 없어야 한다.
assert rt.get("lotcd"), f"lotcd 미상속: {rt}"
assert not lotcd_reask, "lotcd가 채워졌는데 재질문이 떴음 (projection 버그 재발)"
# fail_type 상속: 이어받았으면 선택한 파라미터와 일치해야 한다.
ft_val = str(rt.get("fail_type") or "")
assert ft_val, f"fail_type 미상속(재질문={bool(ft_reask)}): {rt}"
assert param.upper() in ft_val.upper(), f"엉뚱한 fail_type 상속: 기대~{param}, 실제={ft_val!r}"
print(f"   ✅ PASS(이어받음): lotcd={rt.get('lotcd')} fail_type={ft_val}, lotcd/fail_type 재질문 없음")


# ── 2) 이어받지 않음 경로 (독립 요청) ─────────────────────────────
s2 = Session()
t1b = s2.turn("어제 열화리포트 보여줘")
idx2, param2 = pick_real_param((intr(t1b, "postwads_failtype") or {}).get("options") or [])
s2.turn("", resume_value={"postwads_failtype": idx2})
s2.turn("", resume_value={"postwads_choice": "map_agent"})
t5 = s2.turn("5NA 최근 3주 수율 알려줘")
y = t5.slots_for("yield_agent")
print(f"5) 독립요청 planned={t5.planned_agents()} yield.slots={y}")
assert "yield_agent" in t5.planned_agents(), f"yield 미계획: {t5.planned_agents()}"
assert param2.upper() not in str(y).upper(), f"stale 파라미터({param2}) leak: {y}"
assert str(y.get("lotcd") or "").upper() == "5NA", f"lotcd 오류: {y}"
print(f"   ✅ PASS(이어받지 않음): 독립 요청에 stale {param2} 누출 없음")

print("\n===== 전체 PASS =====")
