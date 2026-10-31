from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel, ValidationError


FIXTURE = Path(__file__).with_name("fixtures") / "wafer_set_exposure_baseline.json"
EMPTY_HINT = "필터 결과가 0행입니다. list_values(source_id, field)로 해당 열의 실제 값을 확인하세요"
HIDDEN = {
    "inspect_yield_coverage", "query_wafer_yield", "query_yield",
    "query_defect_yield", "analyze_yield", "render_wafer_map",
    "compare_wafer_maps", "render_yield_scatter",
}
DESCRIPTIONS = {
    "describe_sources": "소스 식별자와 제품·공정 범위 및 정의 포함 여부를 입력받아 등록 데이터 소스의 열·관계·계산 정의와 사용 가능 상태 표를 반환한다. 소스 식별자·제품·공정은 각각 최대 20개다.",
    "list_values": "등록 소스 식별자 또는 저장 표 핸들과 열을 입력받아 고유값·빈도·요약 표를 반환한다. 고유값은 최대 1,000개다.",
    "inspect_coverage": "등록 소스 식별자 또는 저장 표 핸들과 검사 항목을 입력받아 행·누락·중복·시각 범위 및 선택적 연결률 표를 반환한다.",
    "select_wafers": "기간·공정·제품 조건과 계산 기준을 입력받아 wafer별 수율 표와 표 핸들을 반환한다. 출력은 최대 20,000행이다.",
    "read_facts": "등록 소스와 선택 범위 또는 저장 멤버 표를 입력받아 식별자와 출처가 있는 사실 표를 반환한다. 출력은 최대 250,000행·1,000측정·64 MiB 원본 내용이다.",
    "join_facts": "두 저장 표와 등록 관계 및 시각 정책을 입력받아 결과·감사·미연결 표를 반환한다. 출력은 최대 250,000행이며 행 증식은 최대 100배다.",
    "filter_set": "저장 표와 조건 또는 집합 연산을 입력받아 원본 열을 보존한 멤버 표와 감사 표를 반환한다. 출력은 최대 250,000행이다.",
    "make_cohorts": "저장 멤버 표와 그룹 열 또는 이름 있는 조건을 입력받아 집합 표와 소속 표를 반환한다. 집합은 최대 100개, 소속은 최대 250,000행이다.",
    "rank_members": "표 핸들과 정렬·선택 조건을 입력받아 순위 표와 선택된 행의 표 핸들을 반환한다. 선택 수는 그룹별 최대 200개다.",
    "compute_metrics": "저장 측정 또는 사실 표와 지표 명세 및 선택적 정의·소속 표를 입력받아 분자·분모가 있는 지표 표를 반환한다. 지표는 최대 25개, 출력은 최대 250,000행이다.",
    "aggregate_metrics": "최대 10개의 저장 지표 표와 그룹·기간·연산을 입력받아 집계 표 및 선택적 피벗·열 메타데이터 표를 반환한다. 집계 그룹은 최대 100개다.",
    "reduce_maps": "저장 맵 측정과 지표 명세 및 선택적 정의·소속 표를 입력받아 좌표별 분자·분모·비율 표를 반환한다. 지표는 최대 25개, 출력은 최대 250,000좌표다.",
    "compare_metrics": "두 저장 지표 표와 대응 키 및 변화 방식을 입력받아 차이·상대 백분율·비율 중 선택한 비교 표와 감사 표를 반환한다.",
    "render_maps": "입력 표의 각 행마다 wafer 맵 이미지 1개와 재계산 결과표를 반환한다. 입력은 최대 30행이다.",
    "render_view": "저장 표와 열·표시 설정을 입력받아 차트 이미지 또는 표 보기와 원본 해시가 포함된 manifest를 반환한다. 입력은 최대 20,000행이다.",
}
HERMES_APPENDS = {
    "read_result": "저장 표 핸들을 입력하면 최대 1,000행·1 MiB의 원본 페이지와 페이지 메타데이터를 반환한다.",
    "export_report": "저장 표·파일 핸들을 입력하면 선택 내용을 최대 200슬라이드의 PPTX와 내보내기 manifest로 반환한다.",
    "run_python": "저장 표 핸들과 코드를 입력하면 최대 10개·총 250,000행의 표와 최대 3개 차트 및 실행 manifest를 반환한다.",
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def snapshot(tools):
    return [{
        "name": tool["function"]["name"],
        "description": tool["function"]["description"],
        "schema_sha256": hashlib.sha256(canonical(tool)).hexdigest(),
    } for tool in tools]


def baseline():
    return json.loads(FIXTURE.read_text())


class ChainStore:
    def __init__(self):
        self.items = {}

    def add(self, result_id, result, tool_name):
        refs = [SimpleNamespace(**table.model_dump()) for table in result.tables]
        self.items[result_id] = (
            SimpleNamespace(result_id=result_id, tool_name=tool_name,
                summary=result.summary, status=result.status or "success",
                scope=copy.deepcopy(result.scope), tables=refs,
                provenance={"data_origin": "fixture"}),
            {table.table_id: copy.deepcopy(table.rows) for table in result.tables},
        )

    async def observation(self, principal, session, result_id):
        assert (principal, session) == ("owner", "session")
        return self.items[result_id][0]

    async def rows(self, principal, session, result_id, table_id=None):
        await self.observation(principal, session, result_id)
        return copy.deepcopy(self.items[result_id][1][table_id])


def context(store):
    return SimpleNamespace(store=store, run={"principal_id": "owner",
        "session_id": "session", "run_id": "run", "epoch": 1})


def stored_result(table_id, rows, *, columns=None):
    from harness.types import ResultTable
    from harness.tools.registry import ToolResult
    from harness.tools.wafer_set_tools import _version

    columns = columns or list(rows[0])
    return ToolResult(tables=[ResultTable(table_id=table_id, title=table_id,
        rows=rows, columns=columns, complete=True)],
        scope={"handle_contract": "wafer-table/v1",
               "table_versions": {table_id: _version(rows)}})


@pytest.mark.parametrize("flag", [None, "0"])
def test_flag_off_matches_baseline(monkeypatch, flag):
    from harness.tools.registry import domain_registry

    if flag is None:
        monkeypatch.delenv("HARNESS_WAFER_SET_TOOLS", raising=False)
    else:
        monkeypatch.setenv("HARNESS_WAFER_SET_TOOLS", flag)
    tools = domain_registry().model_tools()
    assert len(tools) == 21
    assert snapshot(tools) == baseline()["off"]


def test_flag_on_exposes_only_fixed_wafer_set_tools(monkeypatch):
    from harness.tools.registry import domain_registry
    from harness.tools.wafer_set_register import WAFER_SET_TOOL_NAMES

    monkeypatch.setenv("HARNESS_WAFER_SET_TOOLS", "1")
    registry = domain_registry()
    advertised = {tool["function"]["name"] for tool in registry.model_tools()}
    expected = {
        "aggregate_metrics", "compare_metrics", "compute_metrics", "describe_sources",
        "export_report", "filter_set", "inspect_coverage", "join_facts", "list_values",
        "make_cohorts", "rank_members", "read_facts", "read_result", "reduce_maps",
        "render_maps", "render_view", "run_python", "select_wafers",
    }
    legacy = {item["name"] for item in baseline()["off"]}
    assert len(registry.tools) == 36
    assert advertised == WAFER_SET_TOOL_NAMES == expected
    assert sorted(advertised) == baseline()["on_names"]
    assert legacy <= set(registry.tools)
    for name in legacy - {"read_result", "export_report", "run_python"}:
        assert registry.tools[name].group == "legacy"


def test_replacement_metadata_is_complete_and_safe(monkeypatch):
    from harness.tools.registry import domain_registry
    from harness.tools.wafer_set_register import REPLACES, WAFER_SET_TOOL_NAMES

    monkeypatch.setenv("HARNESS_WAFER_SET_TOOLS", "1")
    registry = domain_registry()
    assert {name for names in REPLACES.values() for name in names} == HIDDEN
    assert set(REPLACES) <= WAFER_SET_TOOL_NAMES
    assert all(owner in registry.tools for owner in REPLACES)
    assert all(owner not in names for owner, names in REPLACES.items())
    assert not {"read_result", "export_report", "run_python"} & HIDDEN
    for name in WAFER_SET_TOOL_NAMES:
        spec = registry.tools[name]
        assert spec.group == "wafer_set"
        assert spec.replaces == REPLACES.get(name, ())


@pytest.mark.asyncio
async def test_generic_registry_filters_in_order_but_hidden_handler_is_callable():
    from harness.tools.registry import ToolRegistry, ToolSpec

    class Input(BaseModel):
        value: int

    async def handler(args, ctx):
        return args.value

    registry = ToolRegistry()
    registry.add(ToolSpec("first", "first", Input, handler))
    registry.add(ToolSpec("replacement", "replacement", Input, handler,
                          replaces=("hidden",)))
    registry.add(ToolSpec("hidden", "hidden", Input, handler))
    registry.add(ToolSpec("last", "last", Input, handler, replaces=()))
    registry.tools["hidden"] = ToolSpec("hidden", "hidden", Input, handler, group="legacy")
    registry.hidden_groups.add("legacy")
    assert [tool["function"]["name"] for tool in registry.model_tools()] == [
        "first", "replacement", "last"]
    assert await registry.invoke("hidden", {"value": 7}, None) == 7


def test_flag_registration_is_isolated(monkeypatch):
    from harness.tools.registry import domain_registry

    monkeypatch.delenv("HARNESS_WAFER_SET_TOOLS", raising=False)
    before = domain_registry()
    monkeypatch.setenv("HARNESS_WAFER_SET_TOOLS", "1")
    domain_registry()
    monkeypatch.delenv("HARNESS_WAFER_SET_TOOLS", raising=False)
    after = domain_registry()
    assert snapshot(before.model_tools()) == snapshot(after.model_tools()) == baseline()["off"]
    assert before.hidden_groups == after.hidden_groups == set()
    assert all(spec.group is None and spec.replaces == () for spec in after.tools.values())


def test_descriptions_have_only_io_limits_and_preserve_hermes_prefixes(monkeypatch):
    from harness.tools.registry import domain_registry

    monkeypatch.setenv("HARNESS_WAFER_SET_TOOLS", "1")
    registry = domain_registry()
    assert set(DESCRIPTIONS) | set(HERMES_APPENDS) == {
        name for name, spec in registry.tools.items() if spec.group == "wafer_set"}
    for name, description in DESCRIPTIONS.items():
        assert registry.tools[name].description == description
    old = {item["name"]: item["description"] for item in baseline()["off"]}
    for name, appended in HERMES_APPENDS.items():
        assert registry.tools[name].description == old[name] + " " + appended


def test_flag_on_schema_size_is_reduced_from_prechange_baseline(monkeypatch):
    from harness.tools.registry import domain_registry

    monkeypatch.setenv("HARNESS_WAFER_SET_TOOLS", "1")
    on_bytes = len(canonical(domain_registry().model_tools()))
    assert on_bytes < baseline()["prechange_on_bytes"]
    # Requirement correction: on < off is intentionally unresolved in this scope.
    assert baseline()["off_bytes"] < on_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize("group_by", [[], ["variant"]])
async def test_duplicate_measurement_hint_and_group_by_limitation(group_by):
    from harness.runtime.domain_process import DomainError
    from harness.tools.wafer_set_aggregate import AggregateMetricsInput, aggregate_metrics

    rows = [{"measurement_ref": "m", "metric_id": "yield", "definition_id": "d",
             "cohort_id": "c", "value": value, "numerator": value,
             "denominator": 100, "unit": "percent", "variant": variant}
            for value, variant in ((90, "a"), (80, "b"))]
    store = ChainStore()
    store.add("metrics", stored_result("metrics", rows), "compute_metrics")
    args = AggregateMetricsInput(sources=[{"result_id": "metrics", "table_id": "metrics"}],
                                 operation="mean", group_by=group_by)
    with pytest.raises((ValueError, DomainError)) as caught:
        await aggregate_metrics(args, context(store))
    message = str(caught.value)
    assert message.startswith("Duplicate measurement has conflicting values")
    for text in ("compute_metrics", "filter_set", "rank_members", "group_by"):
        assert text in message


@pytest.mark.asyncio
async def test_wrong_aggregate_source_has_next_action_hint():
    from harness.tools.wafer_set_aggregate import AggregateMetricsInput, aggregate_metrics

    store = ChainStore()
    store.add("facts", stored_result("facts", [{"lot_id": "L", "wf_id": 1}]), "read_facts")
    args = AggregateMetricsInput(sources=[{"result_id": "facts", "table_id": "facts"}],
                                 operation="mean")
    with pytest.raises(ValueError) as caught:
        await aggregate_metrics(args, context(store))
    message = str(caught.value)
    assert message.startswith("sources는 compute_metrics가 만든 지표 표여야 합니다")
    for text in ("read_facts", "select_wafers", "metrics"):
        assert text in message


@pytest.mark.parametrize("mode", ["neither", "both"])
def test_calculation_mode_validation_preserves_hint_in_serialized_errors(mode):
    from harness.tools.wafer_set_tools import SelectWafersInput

    values = {"process": "PT1H", "time_column": "end_tm",
              "time_from": "2026-01-01T00:00:00", "time_to": "2026-01-02T00:00:00"}
    if mode == "both":
        values.update(pass_bin="A", bin_side="left_bin", symbol_bin_parameters=["x"])
    with pytest.raises(ValidationError) as caught:
        SelectWafersInput.model_validate(values)
    message = caught.value.errors()[0]["msg"]
    assert "Supply exactly one calculation mode" in message
    for name in ("pass_bin", "bin_side", "symbol_bin_parameters"):
        assert name in message
        assert name in json.dumps(caught.value.errors(), ensure_ascii=False, default=str)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["compute_metrics", "reduce_maps"])
async def test_missing_map_payload_has_read_facts_hint(tool_name):
    from harness.tools import wafer_set_metrics, wafer_set_reduce

    rows = [{"measurement_ref": "m", "member_id": "w", "payload_hash": "missing",
             "lot_cd": "P", "process": "PT1H"}]
    store = ChainStore()
    store.add("maps", stored_result("map_measurements", rows), "read_facts")
    metric = {"metric_id": "pass", "mode": "bin", "quantity": "pass_rate",
              "bin_side": "left_bin", "pass_bin": "A"}
    module = wafer_set_metrics if tool_name == "compute_metrics" else wafer_set_reduce
    schema = module.ComputeMetricsInput if tool_name == "compute_metrics" else module.ReduceMapsInput
    handler = module.compute_metrics if tool_name == "compute_metrics" else module.reduce_maps
    args = schema(source={"result_id": "maps", "table_id": "map_measurements"}, metrics=[metric])
    with pytest.raises(ValueError) as caught:
        await handler(args, context(store))
    message = str(caught.value)
    assert message.startswith("Stored map payload table is required")
    assert "include_payload=True" in message
    assert "map_measurements" in message


@pytest.mark.asyncio
async def test_unknown_source_hint_is_raised_before_database_access(monkeypatch):
    from harness.tools import wafer_set_facts

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(wafer_set_facts, "run_blocking", inline)
    args = wafer_set_facts.ReadFactsInput(source_id="not-a-source", query={"lot_ids": ["L"]})
    with pytest.raises(ValueError) as caught:
        await wafer_set_facts.read_facts(args, context(ChainStore()))
    message = str(caught.value)
    assert message.startswith("Unknown source_id: not-a-source")
    assert "describe_sources" in message
    assert "list_values" in message


@pytest.mark.asyncio
async def test_native_bridge_and_nodes_bind_only_advertised_schemas(monkeypatch):
    from harness.config import Settings
    from harness.native_hermes import NativeBridge
    from harness.nodes import Nodes
    from harness.tools.registry import domain_registry

    monkeypatch.setenv("HARNESS_WAFER_SET_TOOLS", "1")
    registry = domain_registry()
    native_names = {tool["function"]["name"] for tool in NativeBridge(
        registry, SimpleNamespace()).schemas()}
    assert native_names == set(baseline()["on_names"])

    seen = []
    class Model:
        def bind_tools(self, tools, **kwargs):
            seen.append({tool["function"]["name"] for tool in tools})
            return self

    async def check():
        return {"usage": {"tokens": 0, "models": 0, "tools": 0}, "epoch": 1}

    async def model_call(*args, **kwargs):
        return AIMessage(content="ready")

    ctx = SimpleNamespace(settings=Settings(), check=check, model_call=model_call)
    node = Nodes(Model(), registry, ctx, "")
    await node.think({"goal": {}, "observations": [], "messages": [],
                      "loaded_tools": ["query_yield", "read_result"]})
    assert "query_yield" not in seen[-1]
    assert "read_result" in seen[-1]
    assert seen[-1] & set(registry.tools) <= set(baseline()["on_names"])


def test_prefix_normalization_cases_and_original_query_immutability(monkeypatch):
    from harness import wafer_set_sql as sql
    from harness.wafer_set_contracts import SourceQuery

    monkeypatch.setattr(sql, "inspect_catalog", lambda source_ids: {
        "wafer_parameter": {"available": True, "columns": {
            "PROCESS": {"data_type": "VARCHAR2"}, "LOTID": {"data_type": "VARCHAR2"},
            "VALUE": {"data_type": "NUMBER"}, "LOT_CD": {"data_type": "VARCHAR2"},
        }},
    })
    calls = []
    values = {
        "process": ["PT1H TEST", "PT1H PROD", "PT1C TEST"],
        "lot_id": ["LOT A", "LOT B"], "lot_cd": ["4SS TEST"],
    }
    def distinct(source_id, column, query, include_null, order, limit):
        calls.append(column)
        rows = [{"value": value, "count": 1, "is_null": False} for value in values.get(column, [])]
        return {"rows": rows, "source_row_count": len(rows),
                "distinct_count": len(rows), "null_count": 0}
    monkeypatch.setattr(sql, "query_distinct_values", distinct)
    original = SourceQuery(lot_cd=["4SS"], process=["PT1C", "pt1h test", "PT1", "PT1H T", ""],
        lot_ids=["LOT", "missing"], where={"mode": "any", "items": [
            {"column": "process", "op": "in", "value": ["PT1C", "PT1"]},
            {"column": "value", "op": "eq", "value": 1.0},
        ]})
    before = original.model_dump()
    normalized, notices = sql.normalize_source_query("wafer_parameter", original)
    assert original.model_dump() == before
    assert normalized.lot_cd == ["4SS TEST"]
    assert normalized.process == ["PT1C TEST", "pt1h test", "PT1", "PT1H T", ""]
    assert normalized.lot_ids == ["LOT", "missing"]
    assert normalized.where.mode == "any"
    assert normalized.where.items[0].value == ["PT1C TEST", "PT1"]
    assert normalized.where.items[1].value == 1.0
    assert {item["code"] for item in notices} == {"FILTER_VALUE_NORMALIZED"}
    assert calls.count("process") == 1
    assert calls.count("lot_cd") == 1
    assert calls.count("lot_id") == 1


def test_prefix_normalization_abstains_for_truncated_distinct_values(monkeypatch):
    from harness import wafer_set_sql as sql
    from harness.wafer_set_contracts import SourceQuery

    monkeypatch.setattr(sql, "inspect_catalog", lambda source_ids: {
        "map": {"available": True, "columns": {"OPER_DET_DESC": {"data_type": "VARCHAR2"}}}})
    monkeypatch.setattr(sql, "query_distinct_values", lambda *args: {
        "rows": [{"value": "PT1H TEST", "count": 1, "is_null": False}],
        "source_row_count": 2, "distinct_count": 2, "null_count": 0})
    query = SourceQuery(process=["PT1H"])
    assert sql.normalize_source_query("map", query) == (query, [])


@pytest.mark.asyncio
async def test_facts_zero_rows_retry_once_with_visible_normalization(monkeypatch):
    from harness.tools import wafer_set_facts as tool
    from harness.wafer_set_sources import SourceBatch

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(tool, "run_blocking", inline)
    calls = []
    def fetch(source_id, query, *args):
        calls.append(query.model_copy(deep=True))
        rows = [] if query.process == ["PT1H"] else [{"process": query.process[0]}]
        return SourceBatch(rows, len(rows), 0, 0, 0, 2, True, None)
    monkeypatch.setattr(tool, "fetch_source", fetch)
    monkeypatch.setattr(tool, "normalize_source_query", lambda source_id, query: (
        query.model_copy(update={"process": ["PT1H TEST"]}),
        [{"code": "FILTER_VALUE_NORMALIZED", "detail": "normalized"}],
    ))
    args = tool.ReadFactsInput(source_id="wafer_parameter", query={"process": ["PT1H"]})
    result = await tool.read_facts(args, context(ChainStore()))
    assert len(calls) == 2
    assert args.query.process == ["PT1H"]
    assert result.scope["requested_query"]["process"] == ["PT1H"]
    assert result.scope["effective_query"]["process"] == ["PT1H TEST"]
    assert result.scope["limitations"][0]["code"] == "FILTER_VALUE_NORMALIZED"


def test_four_actionable_error_hints_are_serialized():
    from harness.tools.wafer_set_discovery import InspectCoverageInput, ListValuesInput
    from harness.tools.wafer_set_metrics import MetricSpec

    for schema, values in (
        (ListValuesInput, {"source": {"result_id": "r", "table_id": "t"}, "field": "x", "query": {}}),
        (InspectCoverageInput, {"source": {"result_id": "r", "table_id": "t"}, "query": {}}),
        (MetricSpec, {"metric_id": "m", "mode": "source_value", "quantity": "value"}),
    ):
        with pytest.raises(ValidationError) as caught:
            schema.model_validate(values)
        serialized = json.dumps(caught.value.errors(), ensure_ascii=False, default=str)
        assert any(text in serialized for text in ("source_id와 함께", "value_column=<"))


@pytest.mark.asyncio
async def test_empty_string_filter_hints_for_stored_filter_and_cohort_are_model_visible(monkeypatch):
    from harness.tools import wafer_set_members as tool
    from harness.types import ToolObservation

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(tool, "run_blocking", inline)
    rows = [{"id": 1, "process": "PT1H TEST"}]
    store = ChainStore()
    store.add("source", stored_result("facts", rows), "read_facts")
    result = await tool.filter_set(tool.FilterSetInput(
        source={"result_id": "source", "table_id": "facts"}, operation="where",
        predicate={"items": [{"column": "process", "op": "eq", "value": "PT1H"}]},
        key_columns=["id"]), context(store))
    assert "list_values(source_id, field)" in result.summary
    assert "저장 표의 실제 값" in result.summary
    assert result.scope["limitations"][-1]["code"] == "EMPTY_STRING_FILTER"
    observation = ToolObservation(principal_id="owner", session_id="session", run_id="run",
        invocation_id="i", result_id="r", tool_name="filter_set", summary=result.summary,
        scope=result.scope, status=result.status or "empty")
    assert "list_values(source_id, field)" in observation.model_view()["summary"]

    cohort = await tool.make_cohorts_handler(tool.MakeCohortsInput(
        source={"result_id": "source", "table_id": "facts"}, key_columns=["id"],
        rules=[{"name": "missing", "predicate": {"items": [
            {"column": "process", "op": "in", "value": ["PT1H"]}]}}]), context(store))
    assert cohort.scope["limitations"][-1]["code"] == "EMPTY_STRING_FILTER"


@pytest.mark.asyncio
async def test_empty_hints_for_facts_and_list_values(monkeypatch):
    from harness.tools import wafer_set_discovery as discovery, wafer_set_facts as facts
    from harness.wafer_set_sources import SourceBatch

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(facts, "run_blocking", inline)
    monkeypatch.setattr(discovery, "run_blocking", inline)
    monkeypatch.setattr(facts, "fetch_source", lambda *args: SourceBatch([], 0, 0, 0, 0, 2, True, None))
    monkeypatch.setattr(facts, "normalize_source_query", lambda source_id, query: (query, []))
    fact_result = await facts.read_facts(facts.ReadFactsInput(
        source_id="wafer_parameter", query={"process": ["NOT FOUND"]}), context(ChainStore()))
    assert fact_result.status == "empty"
    assert EMPTY_HINT in fact_result.summary
    assert fact_result.scope["limitations"][-1]["code"] == "EMPTY_STRING_FILTER"

    monkeypatch.setattr(discovery, "query_distinct_values", lambda *args: {
        "rows": [], "source_row_count": 0, "distinct_count": 0, "null_count": 0})
    monkeypatch.setattr(discovery, "normalize_source_query", lambda source_id, query: (query, []))
    values = await discovery.list_values(discovery.ListValuesInput(
        source_id="wafer_parameter", field="process", query={"process": ["NOT FOUND"]}),
        context(ChainStore()))
    assert values.status == "empty"
    assert EMPTY_HINT in values.summary
    assert values.scope["limitations"][-1]["code"] == "EMPTY_STRING_FILTER"


def test_empty_map_selection_hint_without_labeling_empty_maps(monkeypatch):
    from harness.tools import wafer_set_tools as tool

    monkeypatch.setattr(tool, "_fetch_measurements", lambda args: [])
    monkeypatch.setattr(tool, "_fetch_lot_codes", lambda: set())
    args = tool.SelectWafersInput(process="NOT FOUND", time_column="end_tm",
        time_from="2026-01-01T00:00:00", time_to="2026-01-02T00:00:00",
        pass_bin="A", bin_side="left_bin")
    empty = tool._select_wafers(args)
    assert empty.status == "empty" and EMPTY_HINT in empty.summary
    assert empty.scope["limitations"][-1]["code"] == "EMPTY_STRING_FILTER"


def test_latest_policy_hint_uses_columns_contract(monkeypatch):
    from harness import wafer_set_sources as sources

    cursor = SimpleNamespace(description=[("LOT_ID",)], execute=lambda sql, binds: None,
                             fetchmany=lambda count: [], close=lambda: None, arraysize=1)
    connection = SimpleNamespace(cursor=lambda: cursor, close=lambda: None, call_timeout=1000)
    monkeypatch.setattr(sources, "_connection", lambda: connection)
    with pytest.raises(ValueError) as caught:
        sources.fetch_source("map", None, ["lot_id"], "latest_per_entity", 10, 10_000)
    message = str(caught.value)
    assert "columns에" in message and "measurement_policy='all'" in message


@pytest.mark.asyncio
async def test_render_maps_missing_columns_has_valid_source_hint():
    from harness.tools import wafer_set_tools as tool

    store = ChainStore()
    store.add("facts", stored_result("facts", [{"lot_id": "L"}]), "read_facts")
    args = tool.RenderMapsInput(source={"result_id": "facts", "table_id": "facts"},
                                view="pass_fail")
    with pytest.raises(ValueError) as caught:
        await tool.render_maps(args, context(store))
    message = str(caught.value)
    for text in ("select_wafers", "rank_members", "read_facts"):
        assert text in message


@pytest.mark.asyncio
async def test_retry_preserves_member_constraints_and_nonempty_skips_normalizer(monkeypatch):
    from harness.tools import wafer_set_facts as tool
    from harness.wafer_set_sources import SourceBatch

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(tool, "run_blocking", inline)
    rows = [{"lot_id": "L", "wf_id": 7}]
    store = ChainStore()
    store.add("members", stored_result("members", rows), "filter_set")
    calls = []
    def fetch(source_id, query, *args):
        calls.append(query.model_copy(deep=True))
        found = query.process == ["PT1H TEST"]
        return SourceBatch([{"process": "PT1H TEST"}] if found else [], int(found), 0, 0, 0, 2, True, None)
    monkeypatch.setattr(tool, "fetch_source", fetch)
    normalizations = []
    def normalize(source_id, query):
        normalizations.append(query)
        return query.model_copy(update={"process": ["PT1H TEST"]}), [
            {"code": "FILTER_VALUE_NORMALIZED", "detail": "normalized"}]
    monkeypatch.setattr(tool, "normalize_source_query", normalize)
    result = await tool.read_facts(tool.ReadFactsInput(source_id="wafer_parameter",
        members={"result_id": "members", "table_id": "members"},
        query={"process": ["PT1H"]}), context(store))
    assert len(calls) == 2 and len(normalizations) == 1
    assert calls[0].wafer_pairs == calls[1].wafer_pairs
    assert calls[1].process == ["PT1H TEST"]
    assert result.tables[0].rows

    calls.clear(); normalizations.clear()
    monkeypatch.setattr(tool, "fetch_source", lambda *args: SourceBatch(
        [{"process": "PT1H"}], 1, 0, 0, 0, 2, True, None))
    nonempty = await tool.read_facts(tool.ReadFactsInput(
        source_id="wafer_parameter", query={"process": ["PT1H"]}), context(ChainStore()))
    assert nonempty.tables[0].rows and normalizations == []


@pytest.mark.asyncio
async def test_coverage_retries_by_population_and_links_use_effective_query(monkeypatch):
    from harness.tools import wafer_set_discovery as tool

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(tool, "run_blocking", inline)
    coverage_queries = []
    def coverage(source_id, query, columns, key_columns, time_column, checks):
        coverage_queries.append(query.model_copy(deep=True))
        total = int(query.process == ["PT1H TEST"])
        return [{"check": "rows", "field": "*", "numerator": total,
                 "denominator": total, "value": total, "minimum": None,
                 "maximum": None, "exact": True}]
    monkeypatch.setattr(tool, "query_coverage", coverage)
    monkeypatch.setattr(tool, "normalize_source_query", lambda source_id, query: (
        query.model_copy(update={"process": ["PT1H TEST"]}),
        [{"code": "FILTER_VALUE_NORMALIZED", "detail": "normalized"}],
    ))
    linked_queries = []
    monkeypatch.setattr(tool, "query_link_keys", lambda source_id, query, keys: (
        linked_queries.append(query.model_copy(deep=True)) or [("L", 1)]))
    store = ChainStore()
    store.add("against", stored_result("wafers", [{"lot_id": "L", "wf_id": 1}]), "select_wafers")
    result = await tool.inspect_coverage(tool.InspectCoverageInput(
        source_id="map", query={"process": ["PT1H"]}, checks=["rows", "links"],
        against={"result_id": "against", "table_id": "wafers"},
        relation_id="wafer_identity"), context(store))
    assert [query.process for query in coverage_queries] == [["PT1H"], ["PT1H TEST"]]
    assert linked_queries[0].process == ["PT1H TEST"]
    assert result.scope["effective_query"]["process"] == ["PT1H TEST"]


def test_prefix_normalized_values_remain_bound_sql_parameters():
    from harness.wafer_set_contracts import SourceQuery
    from harness.wafer_set_sql import build_where

    where, binds = build_where("map", SourceQuery(process=["PT1H TEST"]))
    assert "PT1H TEST" not in where
    assert "PT1H TEST" in binds.values()
