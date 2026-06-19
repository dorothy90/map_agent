"""E2E: mining ReAct agent — df_GINI 동적 HTML artifact + 머금기/Q&A(재호출 없음).

  - mining 실행 → df_GINI를 동적 HTML(행 선택·버튼: mn-t + <script>) artifact로 방출
  - 같은 세션 후속 질문 → mining_agent로 라우팅, 머금은 gini 표로 답변(새 artifact 없음)
  - 동일 입력 memo로 mining API(_call_minig_api) 재호출 없음(서버 로그로 확인)

Run (server up on :8001):  python tests/verify_mining_artifact.py
"""
import sys; sys.path.insert(0, "tests")
from e2e_client import Session, server_is_up

assert server_is_up(), "서버(:8001) 미기동"

s = Session()
print("session:", s.session_id)

# 1) mining 실행 → 동적 HTML artifact
t1 = s.turn("4SA DC FAIL Mining 분석해줘")
assert "mining_agent" in t1.planned_agents(), f"mining 미계획: {t1.planned_agents()}"
arts1 = [e for e in t1.sse_events if e.get("type") == "artifact" and e.get("agent") == "mining_agent"]
assert arts1, "mining artifact 미방출"
data = str(arts1[-1].get("data", ""))
assert arts1[-1].get("artifact_type") == "html", f"artifact_type != html: {arts1[-1].get('artifact_type')}"
assert "mn-t" in data and "<script" in data and "data-param" in data, "동적 표(행선택/JS) 마크업 누락"
# Key Value 컬럼 + TAS 버튼 컬럼(서버 호출) 마크업 확인
assert "KEY VALUE" in data.upper(), "Key Value 컬럼 누락"
assert "<th>TAS</th>" in data and "tas-btn" in data, "TAS 컬럼/버튼 누락"
assert "/mining/tas" in data and "data-oper" in data and "data-kv" in data, "TAS fetch/데이터 속성 누락"
print("1) ✅ mining artifact: 동적 표 + Key Value 컬럼 + TAS 버튼(/mining/tas) 방출")

# 1-b) TAS 엔드포인트 직접 호출 (stub 접수 확인)
import httpx
from e2e_client import BASE_URL
r = httpx.post(
    f"{BASE_URL}/mining/tas",
    json={"lotcd": "4SA", "oper_det_desc": "T_OPER_01", "key_value": "KV01", "fail_name": "DC FAIL"},
    timeout=10,
)
assert r.status_code == 200, f"/mining/tas status={r.status_code}"
assert r.json().get("status") == "accepted", f"/mining/tas 응답: {r.json()}"
print("1-b) ✅ POST /mining/tas →", r.json().get("message"))

# 2) 후속 질문 → mining_agent 라우팅 + 머금은 표로 답변(새 artifact 없음)
# planner 라우팅은 LLM이라 가끔 direct-answer로 빠진다(변동성) → 최대 3회 재시도.
for attempt in range(3):
    t2 = s.turn("방금 mining 결과에서 gini 상위 3개 파라미터 알려줘")
    if "mining_agent" in t2.planned_agents():
        break
    print(f"   (재시도 {attempt+1}: planner가 mining 라우팅 안 함 — {t2.planned_agents()})")
assert "mining_agent" in t2.planned_agents(), f"3회 모두 mining 라우팅 실패: {t2.planned_agents()}"
ans = [e for e in t2.sse_events if e.get("type") == "message" and e.get("agent") == "mining_agent"]
assert ans and ans[-1]["content"].strip(), "mining 답변 없음"
new_art2 = [e for e in t2.sse_events if e.get("type") == "artifact" and e.get("agent") == "mining_agent"]
assert not new_art2, "후속 Q&A인데 표 artifact를 다시 그림(memo/cached 분기 실패)"
print("2) ✅ 후속 Q&A: mining_agent 라우팅 + 머금은 표로 답변, 새 artifact 없음")
print("   답변:", ans[-1]["content"][:80].replace("\n", " "))

print("\n===== 전체 PASS =====")
print("(주의: _call_minig_api 재호출 0 여부는 서버 로그 'API 생략'으로 확인 — memo)")
