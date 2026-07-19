# LOT History Common Process Insight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Query multiple LOTs with five batched Oracle statements, find the exact process intersection across all LOTs, and send all intersecting process histories to one LLM call for evidence-backed comparison insight.

**Architecture:** `lot_history_tools.py` owns batched Oracle access and preserves the existing per-LOT result shape. A focused `lot_history_insight.py` converts raw rows into normalized events, computes the all-LOT process intersection, invokes one LLM, and validates every referenced process/LOT/event. `lot_history_agent.py` orchestrates those functions while retaining the original HTML whenever comparison analysis is skipped or fails.

**Tech Stack:** Python 3.10+, Oracle DB driver through `common.get_oracle_connection`, LangChain messages/model invocation, Pydantic v2, Langfuse callbacks, pytest.

## Global Constraints

- Process keys remove leading/trailing whitespace only; preserve case and internal whitespace.
- Never merge similar process names with keywords, regexes, aliases, examples, or LLM classification.
- Q-TIME contributes one outgoing view to `from_oper` and one incoming view to `to_oper`; both views share one `event_id`.
- For N requested LOTs, execute exactly five history SQL statements, one per table, using bind parameters rather than interpolated LOT values.
- “Common process” means the exact set intersection across every requested LOT.
- Invoke the comparison LLM exactly once only when at least two LOTs and a non-empty process intersection exist.
- Preserve the existing detailed HTML and per-LOT `ResultEnvelope.rows` if LLM analysis is skipped or fails.
- Do not silently truncate events to fit the model context.
- Before completion, verify with a real Oracle query, real LLM call, and real server/API scenario.

---

## File Structure

- Modify `08-YieldAgent/lot_history_tools.py`: stable LOT parsing and five batched Oracle queries.
- Create `08-YieldAgent/lot_history_insight.py`: normalized event creation, exact intersection, LLM schemas/call, and semantic reference validation.
- Modify `08-YieldAgent/prompts.py`: one centralized common-process comparison system prompt and one user prompt.
- Modify `08-YieldAgent/lot_history_agent.py`: invoke insight pipeline once, render validated insight, and attach it to the structured result.
- Create `08-YieldAgent/tests/test_lot_history_tools_batch.py`: isolated fake-Oracle tests for five-query behavior.
- Create `08-YieldAgent/tests/test_lot_history_insight.py`: pure transformation, intersection, single-call, and validation tests.
- Create `08-YieldAgent/tests/test_lot_history_agent_insight.py`: node integration and HTML fallback tests with mocked DB/LLM boundary.
- Create `08-YieldAgent/tests/verify_lot_history_common_insight.py`: opt-in real Oracle + real LLM verification script.

### Task 1: Batched Oracle Query

**Files:**
- Modify: `08-YieldAgent/lot_history_tools.py`
- Create: `08-YieldAgent/tests/test_lot_history_tools_batch.py`

**Interfaces:**
- Consumes: `query_lot_history(lot_ids: str) -> str`, `_QUERIES`, `_COLUMN_NAMES`, and `common.get_oracle_connection()`.
- Produces: `_parse_lot_ids(lot_ids: str) -> list[str]` and `_query_lots(conn: Any, lot_ids: list[str]) -> dict[str, dict[str, list[dict[str, Any]]]]`.
- Preserves: `_tool_payload_var["lot_history"] == all_results` with input LOT order and five source keys for every LOT, including zero-row LOTs.

- [ ] **Step 1: Write failing tests for stable LOT parsing and five-query batching**

```python
import pytest

pytestmark = pytest.mark.no_server

from lot_history_tools import _parse_lot_ids, _query_lots


def test_parse_lot_ids_strips_and_deduplicates_in_order():
    assert _parse_lot_ids(" LOT-A,LOT-B,LOT-A ,, LOT-C ") == [
        "LOT-A", "LOT-B", "LOT-C"
    ]


def test_query_lots_executes_once_per_table_and_keeps_empty_lot(fake_history_conn):
    result = _query_lots(fake_history_conn, ["LOT-A", "LOT-B", "LOT-EMPTY"])

    assert len(fake_history_conn.cursor_instance.executions) == 5
    for sql, binds in fake_history_conn.cursor_instance.executions:
        assert " IN (:lot_0, :lot_1, :lot_2)" in sql
        assert binds == {
            "lot_0": "LOT-A", "lot_1": "LOT-B", "lot_2": "LOT-EMPTY"
        }
        assert "LOT-A" not in sql

    assert list(result) == ["LOT-A", "LOT-B", "LOT-EMPTY"]
    assert set(result["LOT-EMPTY"]) == {
        "fdc_alarm", "qtime_over", "trouble_lot", "future_action", "sample_split"
    }
    assert all(rows == [] for rows in result["LOT-EMPTY"].values())
```

Implement `fake_history_conn` in the same test file with a cursor whose `execute()` records `(sql, binds)` and whose `fetchall()` returns table-specific fixture rows containing both `LOT-A` and `LOT-B`. The cursor must return rows in the exact `_COLUMN_NAMES[source]` order so the test also asserts `result["LOT-A"]["fdc_alarm"][0]["lot_id"] == "LOT-A"`.

- [ ] **Step 2: Run the tests and confirm the old per-LOT API fails**

Run:

```bash
cd 08-YieldAgent
pytest -q tests/test_lot_history_tools_batch.py
```

Expected: collection or assertion failure because `_parse_lot_ids` and `_query_lots` do not exist.

- [ ] **Step 3: Implement bind-safe batched querying**

Replace `_query_lot` with these responsibilities:

```python
def _parse_lot_ids(lot_ids: str) -> list[str]:
    seen: set[str] = set()
    parsed: list[str] = []
    for raw in lot_ids.split(","):
        lot_id = raw.strip()
        if lot_id and lot_id not in seen:
            seen.add(lot_id)
            parsed.append(lot_id)
    return parsed


def _query_lots(conn: Any, lot_ids: list[str]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    results = {
        lot_id: {source: [] for source in _QUERIES}
        for lot_id in lot_ids
    }
    binds = {f"lot_{index}": lot_id for index, lot_id in enumerate(lot_ids)}
    placeholders = ", ".join(f":lot_{index}" for index in range(len(lot_ids)))
    cur = conn.cursor()
    try:
        for source, sql_template in _QUERIES.items():
            cur.execute(sql_template.format(lot_placeholders=placeholders), binds)
            columns = _COLUMN_NAMES[source]
            for raw_row in cur.fetchall():
                row = dict(zip(columns, raw_row))
                lot_id = str(row.get("lot_id") or "").strip()
                if lot_id in results:
                    results[lot_id][source].append(row)
    finally:
        cur.close()
    return results
```

Change every `_QUERIES` statement to `WHERE LOT_ID IN ({lot_placeholders})`; keep its existing table columns and chronological `ORDER BY`, adding `LOT_ID` as the first order key. Change `query_lot_history()` to call `_parse_lot_ids()` once and `_query_lots(conn, lot_list)` once.

- [ ] **Step 4: Run batch-query tests and existing server-independent tests**

Run:

```bash
cd 08-YieldAgent
pytest -q tests/test_lot_history_tools_batch.py
pytest -q -m no_server
```

Expected: batch tests pass; existing no-server suite has no new failure.

- [ ] **Step 5: Commit the batch-query change**

```bash
git add 08-YieldAgent/lot_history_tools.py 08-YieldAgent/tests/test_lot_history_tools_batch.py
git commit -m "perf(lot-history): batch multi-lot queries"
```

### Task 2: Exact Common-Process Payload

**Files:**
- Create: `08-YieldAgent/lot_history_insight.py`
- Create: `08-YieldAgent/tests/test_lot_history_insight.py`

**Interfaces:**
- Consumes: `all_results: dict[str, dict[str, list[dict[str, Any]]]]` from Task 1.
- Produces: `build_common_process_history(all_results: dict) -> dict[str, Any]` returning `{"lot_ids": [...], "common_processes": [...], "event_ids": [...]}`.
- Each common process item is `{"process": str, "histories_by_lot": {lot_id: [event, ...]}}`.
- Each event is `{"event_id", "lot_id", "process", "source", "role", "event_time", "details"}` and is JSON serializable.

- [ ] **Step 1: Write failing exact-intersection and Q-TIME tests**

```python
import datetime as dt
import pytest

pytestmark = pytest.mark.no_server

from lot_history_insight import build_common_process_history


def test_build_common_process_history_uses_all_lot_intersection():
    raw = {
        "LOT-A": {
            "fdc_alarm": [
                {"lot_id": "LOT-A", "oper_id": " PHOTO ", "transfer_tm": dt.datetime(2026, 7, 18, 10), "eqp_id": "EA"},
                {"lot_id": "LOT-A", "oper_id": "ONLY-A", "transfer_tm": dt.datetime(2026, 7, 18, 11)},
            ],
            "qtime_over": [], "trouble_lot": [], "future_action": [], "sample_split": [],
        },
        "LOT-B": {
            "fdc_alarm": [{"lot_id": "LOT-B", "oper_id": "PHOTO", "transfer_tm": dt.datetime(2026, 7, 18, 9), "eqp_id": "EB"}],
            "qtime_over": [], "trouble_lot": [], "future_action": [], "sample_split": [],
        },
        "LOT-C": {
            "fdc_alarm": [{"lot_id": "LOT-C", "oper_id": "PHOTO", "transfer_tm": dt.datetime(2026, 7, 18, 8), "eqp_id": "EC"}],
            "qtime_over": [], "trouble_lot": [], "future_action": [], "sample_split": [],
        },
    }

    payload = build_common_process_history(raw)

    assert [item["process"] for item in payload["common_processes"]] == ["PHOTO"]
    histories = payload["common_processes"][0]["histories_by_lot"]
    assert list(histories) == ["LOT-A", "LOT-B", "LOT-C"]
    assert histories["LOT-A"][0]["event_time"] == "2026-07-18T10:00:00"


def test_qtime_views_share_event_id_but_keep_from_and_to_roles():
    raw = make_two_lot_history_with_qtime("PHOTO", "ETCH")
    payload = build_common_process_history(raw)
    by_process = {item["process"]: item for item in payload["common_processes"]}

    outgoing = by_process["PHOTO"]["histories_by_lot"]["LOT-A"][0]
    incoming = by_process["ETCH"]["histories_by_lot"]["LOT-A"][0]
    assert outgoing["role"] == "qtime_outgoing"
    assert incoming["role"] == "qtime_incoming"
    assert outgoing["event_id"] == incoming["event_id"]


def test_process_matching_preserves_case_and_internal_whitespace():
    raw = make_histories_with_processes("PHOTO A", "Photo A")
    assert build_common_process_history(raw)["common_processes"] == []
```

Add fixture builders in the same test file returning all five source keys. Also test that `action_step`, `step_desc`, and `sample_split.step` contribute process keys while `sample_split.oper_desc` does not.

- [ ] **Step 2: Run transformation tests and verify failure**

Run:

```bash
cd 08-YieldAgent
pytest -q tests/test_lot_history_insight.py -k 'intersection or qtime or matching'
```

Expected: import failure because `lot_history_insight.py` does not exist.

- [ ] **Step 3: Implement normalization and exact intersection**

In `lot_history_insight.py`, define the source mapping without natural-language aliases:

```python
_SOURCE_PROCESS_FIELDS = {
    "fdc_alarm": (("oper_id", "fdc_alarm"),),
    "qtime_over": (("from_oper", "qtime_outgoing"), ("to_oper", "qtime_incoming")),
    "trouble_lot": (("step_desc", "trouble_lot"),),
    "future_action": (("action_step", "future_action"),),
    "sample_split": (("step", "sample_split"),),
}

_SOURCE_TIME_FIELDS = {
    "fdc_alarm": "transfer_tm",
    "qtime_over": "event_tm",
    "trouble_lot": "hold_time",
    "future_action": "action_time",
    "sample_split": None,
}
```

Generate the base event ID as `f"{lot_id}:{source}:{row_index}"` before creating Q-TIME views. Convert datetime/date/time values recursively to ISO strings; keep other JSON scalar values and convert unsupported values to strings. Build each LOT's process set, compute `set.intersection`, sort process names for deterministic prompts, and include only common process events in `histories_by_lot`. Sort events by `(event_time is None, event_time or "", event_id, role)`.

- [ ] **Step 4: Run all transformation tests**

Run:

```bash
cd 08-YieldAgent
pytest -q tests/test_lot_history_insight.py
```

Expected: all current transformation tests pass.

- [ ] **Step 5: Commit the common-process payload**

```bash
git add 08-YieldAgent/lot_history_insight.py 08-YieldAgent/tests/test_lot_history_insight.py
git commit -m "feat(lot-history): build common process payload"
```

### Task 3: One Validated LLM Comparison

**Files:**
- Modify: `08-YieldAgent/prompts.py`
- Modify: `08-YieldAgent/lot_history_insight.py`
- Modify: `08-YieldAgent/tests/test_lot_history_insight.py`

**Interfaces:**
- Consumes: Task 2 payload, `RunnableConfig`, `common.get_llm`, `common.extract_json_from_llm`, and `lf_utils.lf_callbacks`.
- Produces: `analyze_common_process_history(payload: dict[str, Any], config: RunnableConfig, model: Any | None = None) -> dict[str, Any]`.
- Raises: `ValueError` for schema or semantic-reference violations; model transport exceptions pass to the caller.

- [ ] **Step 1: Add failing tests for exactly one call and semantic validation**

```python
def test_analyze_common_process_history_invokes_model_once(valid_payload):
    model = FakeModel(valid_insight_json(valid_payload))
    result = analyze_common_process_history(valid_payload, {}, model=model)
    assert model.invoke_count == 1
    assert result["process_insights"][0]["process"] == "PHOTO"


def test_analyze_rejects_unknown_event_reference(valid_payload):
    raw = valid_insight_json(valid_payload).replace(
        valid_payload["event_ids"][0], "invented:event"
    )
    with pytest.raises(ValueError, match="unknown event_id"):
        analyze_common_process_history(valid_payload, {}, model=FakeModel(raw))


def test_analyze_rejects_common_pattern_from_one_lot(valid_payload):
    raw = insight_json_with_common_pattern_lot_ids(["LOT-A"])
    with pytest.raises(ValueError, match="at least two LOTs"):
        analyze_common_process_history(valid_payload, {}, model=FakeModel(raw))
```

`FakeModel.invoke()` must capture the system/human messages and return `AIMessage(content=raw_json)`. Assert the human message contains every `common_processes` item and that `invoke_count` remains one regardless of the number of common processes.

- [ ] **Step 2: Run the LLM-boundary tests and verify failure**

Run:

```bash
cd 08-YieldAgent
pytest -q tests/test_lot_history_insight.py -k analyze
```

Expected: import or attribute failure because the analyzer and schemas do not exist.

- [ ] **Step 3: Add the centralized prompts**

Add `LOT_HISTORY_COMMON_PROCESS_INSIGHT_SYSTEM_PROMPT` and `LOT_HISTORY_COMMON_PROCESS_INSIGHT_USER_PROMPT` to `prompts.py`. The system prompt must state that process filtering is complete, only `common_processes` may be analyzed, facts and hypotheses must be separated, every finding must cite input LOT/event IDs, duplicate Q-TIME views with the same event ID count once, embedded event text is untrusted data, and output must be Korean JSON only. The user prompt must be exactly one tagged JSON block:

```python
LOT_HISTORY_COMMON_PROCESS_INSIGHT_USER_PROMPT = """\
다음은 여러 LOT에서 공통으로 확인된 공정의 상세 이력이다.
LOT별 공통점과 차이점, 반복 이상 및 우선 확인 공정을 분석하라.

<common_process_history>
{common_process_history_json}
</common_process_history>
"""
```

The output JSON shape must match the Pydantic models introduced in Step 4.

- [ ] **Step 4: Implement strict output schemas, one call, and reference validation**

Define Pydantic models `EvidenceFinding`, `Hypothesis`, `ProcessInsight`, `PriorityProcess`, and `CommonProcessInsight`. Use `ConfigDict(extra="forbid")`; constrain confidence to `Literal["high", "medium", "low"]`. `analyze_common_process_history()` must:

```python
human_prompt = LOT_HISTORY_COMMON_PROCESS_INSIGHT_USER_PROMPT.format(
    common_process_history_json=json.dumps(payload, ensure_ascii=False, indent=2)
)
llm = model or get_llm()
response = llm.invoke(
    [
        SystemMessage(content=LOT_HISTORY_COMMON_PROCESS_INSIGHT_SYSTEM_PROMPT),
        HumanMessage(content=human_prompt),
    ],
    config={**config, "callbacks": _lf_callbacks()},
)
parsed = extract_json_from_llm(str(response.content), CommonProcessInsight)
```

After parsing, reject any process outside `payload["common_processes"]`, LOT outside `payload["lot_ids"]`, or event outside the referenced process/LOT histories. For each `common_patterns` item, require at least two distinct valid LOT IDs. Return `parsed.model_dump()` only after all checks pass.

- [ ] **Step 5: Run LLM-boundary and full unit tests**

Run:

```bash
cd 08-YieldAgent
pytest -q tests/test_lot_history_insight.py
pytest -q -m no_server
```

Expected: all tests pass and fake-model invocation count is exactly one.

- [ ] **Step 6: Commit the prompt and analyzer**

```bash
git add 08-YieldAgent/prompts.py 08-YieldAgent/lot_history_insight.py 08-YieldAgent/tests/test_lot_history_insight.py
git commit -m "feat(lot-history): analyze common processes once"
```

### Task 4: Agent, HTML, and Result Contract Integration

**Files:**
- Modify: `08-YieldAgent/lot_history_agent.py`
- Create: `08-YieldAgent/tests/test_lot_history_agent_insight.py`

**Interfaces:**
- Consumes: `build_common_process_history()` and `analyze_common_process_history()` from Tasks 2–3.
- Produces: `_render_common_process_insight(insight: dict[str, Any] | None, status: str) -> str` and an extended `_render_lot_history_html(all_results, common_process_insight=None, insight_status="skipped") -> str`.
- Preserves: existing `lot_history_artifacts`, per-LOT count rows, and original five detail sections.

- [ ] **Step 1: Write failing node tests for call gates and failure fallback**

```python
@pytest.mark.no_server
def test_multi_lot_common_process_calls_llm_once(monkeypatch, multi_lot_storage):
    analyzer = Mock(return_value=valid_common_insight())
    monkeypatch.setattr(lot_history_agent, "analyze_common_process_history", analyzer)
    monkeypatch.setattr(lot_history_agent.query_lot_history, "invoke", fake_tool_invoke(multi_lot_storage))

    result = lot_history_agent.lot_history_agent_node(make_state(["LOT-A", "LOT-B"]), {})

    analyzer.assert_called_once()
    assert "공통 공정 비교" in result["lot_history_artifacts"][0]["data"]


@pytest.mark.no_server
@pytest.mark.parametrize("lot_ids,storage", [(["LOT-A"], single_lot_storage()), (["LOT-A", "LOT-B"], no_intersection_storage())])
def test_comparison_is_skipped_without_multiple_lots_and_intersection(monkeypatch, lot_ids, storage):
    analyzer = Mock()
    monkeypatch.setattr(lot_history_agent, "analyze_common_process_history", analyzer)
    monkeypatch.setattr(lot_history_agent.query_lot_history, "invoke", fake_tool_invoke(storage))

    result = lot_history_agent.lot_history_agent_node(make_state(lot_ids), {})

    analyzer.assert_not_called()
    assert result["lot_history_artifacts"]


@pytest.mark.no_server
def test_llm_failure_keeps_original_detail_html(monkeypatch, multi_lot_storage):
    monkeypatch.setattr(lot_history_agent, "analyze_common_process_history", Mock(side_effect=ValueError("bad output")))
    monkeypatch.setattr(lot_history_agent.query_lot_history, "invoke", fake_tool_invoke(multi_lot_storage))

    result = lot_history_agent.lot_history_agent_node(make_state(["LOT-A", "LOT-B"]), {})
    html = result["lot_history_artifacts"][0]["data"]
    assert "비교 분석을 생성하지 못했습니다" in html
    assert "FDC ALARM" in html
    assert "TROUBLE LOT" in html
```

The test helper must set `_tool_payload_var` exactly as the real decorated tool does. Add an assertion that `lot_history_sql_result.additional_kwargs["lot_history_result"]["common_process_insight"]` contains the validated result and that the final result envelope still has one row per LOT.

- [ ] **Step 2: Run agent integration tests and verify failure**

Run:

```bash
cd 08-YieldAgent
pytest -q tests/test_lot_history_agent_insight.py
```

Expected: import/signature failure because the node has not integrated the new functions.

- [ ] **Step 3: Integrate the single-call comparison with explicit status**

After `lot_history_data` is extracted and before HTML rendering:

```python
common_payload: dict[str, Any] = {}
common_insight: dict[str, Any] | None = None
insight_status = "skipped_single_lot"

if isinstance(lot_history_data, dict) and "error" not in lot_history_data:
    common_payload = build_common_process_history(lot_history_data)
    if len(common_payload["lot_ids"]) >= 2:
        if common_payload["common_processes"]:
            try:
                common_insight = analyze_common_process_history(common_payload, config)
                insight_status = "success"
            except Exception as exc:
                logger.error("[LOT History Agent] 공통 공정 분석 실패: %s", exc, exc_info=True)
                insight_status = "analysis_failed"
        else:
            insight_status = "empty_intersection"
```

Do not re-raise LLM or validation exceptions: the original history is a valid result. Keep the existing Oracle error handling unchanged.

- [ ] **Step 4: Render escaped insight and attach structured output**

Add a compact insight card before the existing summary table. Escape every LLM string with `_h`; render `summary`, each process's common patterns and LOT differences, hypotheses with confidence, recommended checks, and priority processes. Render clear Korean status text for `empty_intersection` and `analysis_failed`; render nothing for `skipped_single_lot`.

Pass the insight and status into `_render_lot_history_html()`. Add these fields to `lot_history_sql_result.additional_kwargs["lot_history_result"]`:

```python
{
    "lot_ids": list(per_lot_summary),
    "per_lot_summary": per_lot_summary,
    "common_processes": [item["process"] for item in common_payload.get("common_processes", [])],
    "common_process_insight": common_insight,
    "insight_status": insight_status,
}
```

Keep `ResultEnvelope.rows` as the existing `lot_rows`; add only `common_process_count` and `insight_status` to envelope metadata. When insight succeeds, use its validated `summary` as the leading user message followed by the deterministic LOT count summary.

- [ ] **Step 5: Run focused and regression tests**

Run:

```bash
cd 08-YieldAgent
pytest -q tests/test_lot_history_agent_insight.py tests/test_lot_history_insight.py tests/test_lot_history_tools_batch.py
pytest -q -m no_server
```

Expected: all focused tests pass; no existing no-server regression fails.

- [ ] **Step 6: Commit agent integration**

```bash
git add 08-YieldAgent/lot_history_agent.py 08-YieldAgent/tests/test_lot_history_agent_insight.py
git commit -m "feat(lot-history): show common process insight"
```

### Task 5: Real Oracle, LLM, and API Verification

**Files:**
- Create: `08-YieldAgent/tests/verify_lot_history_common_insight.py`
- Modify only if verification exposes a scoped defect: files from Tasks 1–4 and their focused tests.

**Interfaces:**
- Consumes: real environment configuration already used by `common.get_oracle_connection()` and `common.get_llm()`.
- Produces: an executable verification that exits non-zero unless batch query, non-empty intersection, real LLM validation, and rendered artifact checks pass.

- [ ] **Step 1: Write the opt-in real verification script**

The script must accept a comma-separated CLI argument, avoiding hardcoded production LOTs:

```python
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lot-ids", required=True)
    args = parser.parse_args()
    lot_ids = _parse_lot_ids(args.lot_ids)
    if len(lot_ids) < 2:
        raise SystemExit("--lot-ids requires at least two LOTs")

    conn = _get_oracle_connection()
    try:
        all_results = _query_lots(conn, lot_ids)
    finally:
        conn.close()
    payload = build_common_process_history(all_results)
    if not payload["common_processes"]:
        raise SystemExit("selected LOTs have no exact common process")
    insight = analyze_common_process_history(payload, {})
    html = _render_lot_history_html(all_results, insight, "success")
    assert insight["process_insights"]
    assert "공통 공정 비교" in html
    assert all(lot_id in html for lot_id in lot_ids)
    print(json.dumps({
        "lot_ids": lot_ids,
        "common_processes": [item["process"] for item in payload["common_processes"]],
        "summary": insight["summary"],
    }, ensure_ascii=False, indent=2))
    return 0
```

Instrument or wrap the real cursor in the script so it reports and asserts exactly five `execute()` calls without changing production query behavior.

- [ ] **Step 2: Run real Oracle + real LLM verification**

Use two or more known-valid LOT IDs from the current user scenario:

```bash
cd 08-YieldAgent
python tests/verify_lot_history_common_insight.py --lot-ids 'TSSHNCV,TSSH20Y'
```

Expected: exit 0, `execute_count: 5`, at least one exact common process, a validated Korean LLM summary, and rendered HTML assertions passing. If these LOTs have no current data/intersection, inspect the actual returned process sets and rerun with two valid LOTs; report the exact IDs used rather than claiming the first command passed.

- [ ] **Step 3: Run the real server/API user scenario**

Start the server using the repository's normal environment:

```bash
cd 08-YieldAgent
uvicorn agent_server:app --port 8001
```

In another terminal, run the existing E2E client with a query containing the verified LOT IDs:

```bash
cd 08-YieldAgent
python tests/e2e_client.py 'TSSHNCV,TSSH20Y LOT 이력 비교해줘'
```

Expected: one `lot_history_report` HTML artifact, a user-facing common-process summary, no interrupt, and server logs showing five Oracle history statements plus one comparison LLM invocation. Adapt only the CLI syntax if `tests/e2e_client.py --help` shows a named query flag.

- [ ] **Step 4: Run the full relevant regression suite**

Run:

```bash
cd 08-YieldAgent
pytest -q tests/test_lot_history_tools_batch.py tests/test_lot_history_insight.py tests/test_lot_history_agent_insight.py
pytest -q -m no_server
pytest -q tests/test_e2e_regression.py
```

Expected: focused and no-server suites pass; E2E regression passes when its required live server/LLM dependencies are available, otherwise pytest reports dependency skips rather than feature failures.

- [ ] **Step 5: Review the final diff for surgical scope**

Run:

```bash
git diff --check
git status --short
git diff --stat HEAD~4..HEAD
```

Expected: no whitespace errors; only the files listed in this plan are part of the feature commits. Preserve and report unrelated pre-existing worktree changes.

- [ ] **Step 6: Commit verification support or scoped fixes**

```bash
git add 08-YieldAgent/tests/verify_lot_history_common_insight.py
git commit -m "test(lot-history): verify common process insight"
```

If Step 2 or 3 required a scoped fix, include the affected production file and its regression test in this commit and state the reproduced failure in the commit body.
