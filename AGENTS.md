# AGENTS.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:

- [ ] Remove imports/variables/functions that YOUR changes made unused.
- [ ] Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. End-to-End 검증 필수

**Plan 완료 후 반드시 실제 실행으로 검증한다.**

- lint/syntax 체크는 검증이 아니다. 실제 데이터로 end-to-end 테스트를 수행하라.
- agent 수정 시: 실제 DB 조회, 실제 LLM 호출, 실제 도구 실행까지 확인한다.
- "테스트 통과"를 선언하기 전에 사용자 시나리오를 직접 재현하라.
- 단위 테스트만으로 완료 선언하지 말 것 — 통합 동작이 맞는지 반드시 확인.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## 6. Codex Hardcoding Ban

**Do not solve semantic planning failures with hardcoded natural-language rules.**

- Codex는 자연어 해석 문제를 keyword, regex, phrase list, if/else trigger, enum matching 확장으로 해결하지 않는다.
- Codex는 실패 로그에 나온 특정 문구를 그대로 코드 조건으로 박지 않는다.
- Codex는 "이번 케이스만 통과"시키기 위한 hardcoded branch, special-case parser, Korean expression table을 추가하지 않는다.
- Codex는 few-shot 예시를 계속 늘려 planner를 보정하는 방식으로 문제를 해결하지 않는다.
- Codex는 multi-turn/follow-up 실패를 reference resolver 규칙 추가로 우회하지 않는다.
- 먼저 LLM에 제공되는 context, 직전 assistant message, structured result envelope, canonical request contract가 충분한지 점검한다.
- keyword/regex/하드코딩이 정말 필요한 경우에는 수정 전에 사용자에게 이유, 대안, 적용 범위를 설명하고 명시적 승인을 받아야 한다.
