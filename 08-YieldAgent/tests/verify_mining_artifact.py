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
print("1) ✅ mining 실행 + 동적 HTML artifact(행 선택·버튼) 방출")

# 2) 후속 질문 → mining_agent 라우팅 + 머금은 표로 답변(새 artifact 없음)
t2 = s.turn("방금 mining 결과에서 gini 상위 3개 파라미터 알려줘")
assert "mining_agent" in t2.planned_agents(), f"후속이 mining으로 라우팅 안 됨: {t2.planned_agents()}"
ans = [e for e in t2.sse_events if e.get("type") == "message" and e.get("agent") == "mining_agent"]
assert ans and ans[-1]["content"].strip(), "mining 답변 없음"
new_art2 = [e for e in t2.sse_events if e.get("type") == "artifact" and e.get("agent") == "mining_agent"]
assert not new_art2, "후속 Q&A인데 표 artifact를 다시 그림(memo/cached 분기 실패)"
print("2) ✅ 후속 Q&A: mining_agent 라우팅 + 머금은 표로 답변, 새 artifact 없음")
print("   답변:", ans[-1]["content"][:80].replace("\n", " "))

print("\n===== 전체 PASS =====")
print("(주의: _call_minig_api 재호출 0 여부는 서버 로그 'API 생략'으로 확인 — memo)")
