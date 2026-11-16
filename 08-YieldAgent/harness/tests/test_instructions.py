import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_load_native_instructions_includes_notes_once():
    from harness.instructions import load_native_instructions

    directory = Path(__file__).parents[1] / "instructions"
    native = (directory / "native.md").read_text(encoding="utf-8").strip()
    notes = (directory / "domain-notes.md").read_text(encoding="utf-8").strip()
    loaded = load_native_instructions()

    assert loaded == native + "\n\n" + notes
    assert loaded.count(notes) == 1
    assert "lot은 LOT_ID를 뜻하며, LOT_CD는 제품 코드다." in loaded
    assert "DF_DIE_TO_WF_YLD는 더 이상 사용하지 않으므로 조회하지 않는다." in loaded
    assert "item PARAM(VTH, FMAX 등)은 테스트 측정값" in loaded


def test_load_native_instructions_is_cwd_independent(monkeypatch, tmp_path):
    from harness.instructions import load_native_instructions

    expected = load_native_instructions()
    monkeypatch.chdir(tmp_path)
    assert load_native_instructions() == expected


def test_yield_report_recipe_is_native_only_and_keeps_contract():
    from harness.instructions import load_native_instructions
    from harness.tools.registry import ToolRegistry
    from harness.tools.table_artifact import register

    directory = Path(__file__).parents[1] / "instructions"
    native = (directory / "native.md").read_text(encoding="utf-8")
    notes = (directory / "domain-notes.md").read_text(encoding="utf-8")
    registry = ToolRegistry()
    register(registry)
    tool_description = registry.tools["render_yield_table"].description

    for field in ("lot_cd", "period", "report_scope_id", "grouping", "membership", "group_period"):
        assert field in native
    for phrase in ("DF_ALL_PARA_PRB", "YIELD_TABLE='o'", "PARAM_SEQNO", "x100 소수 1자리",
                   "기준 측정일의 버킷을 포함하고 오래된 버킷부터", "최신 DB 측정일", "base_date를 전달하지 않는다",
                   "today-28 days", "report_totals", "monthly_counts", "weekly_counts", "daily_counts",
                   "group_id", "lot_id", "table_id", "disjoint_subsets", "partition", "comparison",
                   "3번째", "5번째", "동일한 인자", "저장된 성공"):
        assert phrase in native
    for removed in ("long_result_id", "counts_result_id", "gms_result_id", "parameter_formats", "product_label"):
        assert removed not in native
    assert "결과에 표가 전체 1개뿐이고" in native
    assert "같은 사용자·세션·실행(run)·epoch·runtime 안에서만" in native
    assert "report_scope_id" in tool_description
    assert "수율 리포트는" not in notes
    assert "PARAM_NM을 필터링하지 않고 모든 PARAM_NM을" not in load_native_instructions()


def test_native_yield_questions_clarify_all_missing_product_and_period_together():
    from harness.instructions import load_native_instructions

    instructions = load_native_instructions()

    assert "`clarify`" in instructions
    assert "제품이나 기간이 없으면" in instructions
    assert "한 번의 `clarify` 호출로 누락된 항목을 모두 함께" in instructions
    assert "제품과 기간이 모두 없으면 같은 질문/카드에서 함께" in instructions
    assert "기간 기본값이 명백해 보여도 누락된 제품은 반드시" in instructions
    assert "최근 4주" in instructions
    assert "다시 묻지" in instructions
    assert "'많다'" in instructions
    assert "모델이 판단" in instructions
    assert "실제 제품 코드를 조회" in instructions
    assert "선택지를 지어내지" in instructions


def test_native_wafer_map_layout_is_responsive_for_arbitrary_groups():
    from harness.instructions import load_native_instructions

    instructions = load_native_instructions()

    assert "고정 `layout.width`를 지정하지 않고" in instructions
    assert "`autosize=True`" in instructions
    assert "여러 행으로 나누며" in instructions
    assert "행 수에 맞는 `height`" in instructions


def test_native_cummap_definition_and_evidence_contract_are_explicit():
    from harness.instructions import load_native_instructions

    instructions = load_native_instructions()
    for phrase in (
        "fail_count / observation_count", "미관측", "NaN", "1장", "0 또는 1",
        "선택 집합", "명시한 그룹", "lot별", "전체 합산", "clarify",
        "분자", "분모", "재측정", "wafer_map_manifest", "wafer_map_members",
        "wafer_map_cells", "원본 result_id", "맵만", "placeholder",
    ):
        assert phrase in instructions


def test_verifier_requires_structured_map_evidence_and_independent_reaggregation():
    verifier = (Path(__file__).parents[1] / "instructions" / "verifier.md").read_text(encoding="utf-8")
    for phrase in (
        "wafer_map_manifest", "wafer_map_members", "wafer_map_cells",
        "load_wafer_maps", "독립 재집계", "fail_count", "observation_count",
        "fail_rate", "누락", "중복", "binmap", "reject",
    ):
        assert phrase in verifier


def test_missing_domain_notes_raises(monkeypatch):
    import harness.instructions as instructions

    original = Path.read_text

    def missing_notes(path, *args, **kwargs):
        if path.name == "domain-notes.md":
            raise FileNotFoundError(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(instructions.Path, "read_text", missing_notes)
    with pytest.raises(FileNotFoundError):
        instructions.load_native_instructions()


def test_native_startup_contains_domain_notes(monkeypatch, tmp_path):
    import harness.native_hermes as native
    from harness.config import Settings
    from harness.tools.registry import ToolRegistry

    profile = tmp_path / "profile"
    profile.mkdir()
    writes = []

    class Stdin:
        def write(self, data):
            writes.append(data)

        async def drain(self):
            pass

    class Stdout:
        def __init__(self):
            self.lines = [json.dumps({
                "type": "result", "result": {"completed": False, "messages": []},
            }).encode() + b"\n"]

        async def readline(self):
            return self.lines.pop(0) if self.lines else b""

    process = SimpleNamespace(stdin=Stdin(), stdout=Stdout(), returncode=0)

    async def create_process(*args, **kwargs):
        return process

    class Runs:
        async def update_one(self, *args, **kwargs):
            pass

    context = SimpleNamespace(
        settings=Settings(engine="hermes"),
        run={
            "run_id": "run-1", "session_id": "session-1", "principal_id": "alice",
            "epoch": 1, "query": "수율을 보여줘", "created_at": "2026-11-01T09:00:00+09:00",
        },
        store=SimpleNamespace(runs=Runs()),
        remaining_seconds=lambda: 30,
        check=lambda: asyncio.sleep(0),
    )

    monkeypatch.setattr(native, "ROOT", tmp_path)
    monkeypatch.setattr(native, "prepare_profile", lambda *args: profile)
    monkeypatch.setattr(native.asyncio, "create_subprocess_exec", create_process)
    asyncio.run(native.run_native(context, ToolRegistry()))

    startup = json.loads(writes[0])
    assert "검사 단계가 명시되지 않은 “수율”은 PT1H를 뜻한다." in startup["instructions"]
    assert "서버가 제공한 요청 기준정보(JSON)다." in startup["instructions"]
    assert startup["request_context"] == {
        "requested_at": "2026-11-01T09:00:00+09:00", "timezone": "Asia/Seoul",
    }


def test_native_unanswered_group_criterion_stops_without_inventing_defaults():
    native = (Path(__file__).parents[1] / "instructions" / "native.md").read_text(encoding="utf-8")
    for sentence in (
        "사용자가 '저수율'만 말했거나 그룹 구성원을 바꾸는 기준이 누락되었으면 `clarify`로 기준을 묻는다.",
        "`clarify`가 timed_out, cancelled, 비어 있거나 누락된 answers를 반환하거나 그 밖에 사용자 기준을 받지 못했으면 임계값이나 규칙을 지어내지 않는다.",
        "85%, 80%, 하위 10%, 모든 백분위수(percentile), 평균±σ, '데이터 기반 기본값'도 금지한다.",
        "임의 기준으로 그룹을 만들기 위해 `run_python`이나 `render_yield_table`을 호출하지 않는다.",
        "사용자 기준이 여전히 필요하다고 명확히 알리고 해당 턴을 종료한다.",
    ):
        assert sentence in native


def test_native_group_reports_use_optional_comparison_and_required_scope():
    native = (Path(__file__).parents[1] / "instructions" / "native.md").read_text(encoding="utf-8")
    assert "`comparison={current,baseline}`은 선택 사항이며 사용자가 특정 비교를 요청할 때만 전달한다." in native
    assert "표시 순서의 마지막 그룹 − 첫 그룹 Δ" in native
    assert "`report_scope_id`(또는 lot_cd+period)와 `grouping.membership`이 필수이며 coverage는 grouping 안에 둔다." in native
    assert "저수율 vs 나머지는 current에 저수율 그룹 ID, baseline에 나머지 그룹 ID를 전달한다." in native
    assert "Δ의 방향은 current-baseline이다." in native
    assert "그룹 ID를 명시할 때만 만든다" not in native


def test_native_preserves_requested_group_count_and_complement():
    native = (Path(__file__).parents[1] / "instructions" / "native.md").read_text(encoding="utf-8")
    for sentence in (
        "사용자가 N그룹(예: '2그룹')으로 나누라고 하면 정확히 N개 그룹을 만든다.",
        "'저수율(자재)랑 나머지'는 저수율 그룹과 같은 리포트 범위의 나머지 모든 lot을 하나로 묶은 여집합 그룹, 정확히 2개다.",
        "중수율/고수율, '평균 구간', 제3의 잔여 그룹을 임의로 추가하지 않는다.",
        "사용자가 기준에 답한 뒤에도 요청이 2그룹이면 정확히 2개 그룹을 유지한다.",
    ):
        assert sentence in native


def test_native_recent_period_preserves_user_units():
    native = (Path(__file__).parents[1] / "instructions" / "native.md").read_text(encoding="utf-8")
    for sentence in (
        "'최근 N주'는 weeks:N, '최근 N개월/N달'은 months:N, '최근 N일'은 days:N으로 전달하며 단위를 환산하지 않는다.",
        '최근 4주는 {kind:"recent",weeks:4}이며 days:28로, 최근 3개월은 months:3이며 days:90이나 weeks:12로 바꾸지 않는다.',
        "'주별/월별/일별'은 표시 집계 단위 grains이며 기간 단위가 아니다.",
        '최근 4주 주별은 {kind:"recent",weeks:4,grains:["week"]}다.',
    ):
        assert sentence in native


def test_same_time_fabout_uses_measurement_window():
    directory = Path(__file__).parents[1] / "instructions"
    native = (directory / "native.md").read_text(encoding="utf-8")
    notes = (directory / "domain-notes.md").read_text(encoding="utf-8")
    assert "'동일시점 팹아웃' 요청은 실제 FABOUT 날짜가 아니라 동일한 PT 측정일(measurement-date) 범위로 조회한다." in native
    assert "동일시점 팹아웃(사용자 확인, 2026-11-15): 동일한 PT 측정일 범위를 뜻하며 실제 FABOUT 날짜 범위와는 구분한다." in notes

def test_domain_notes_describe_wf_legend_join_and_forbid_substitutes():
    notes = (Path(__file__).parents[1] / "instructions" / "domain-notes.md").read_text(encoding="utf-8")
    for phrase in (
        "### DF_WF_LEGEND",
        "한 wafer는 여러 OPER_ID 행을 가진다",
        "OPER_DET_DESC는 공정 설명이다",
        "WF_ID=WFID",
        "WF_ID는 선행 0 없는 문자다",
        "EQP_ID(장비)",
        "EQP_CH(챔버)",
        "포토 공정에만 값",
        "RWK_CNT(리워크 횟수)",
        "END_TM(공정 종료 시각 'YYYY-MM-DD HH24:MI:SS')",
        "LOT_ID 및 WF_ID=WFID로 조인해 query_db/run_python으로 멤버십을 조회·구성한다",
        "모든 공식 그룹 수율 표는 render_yield_table로 렌더링",
        "임의 중복 제거 없이 clarify로 분류 기준을 확인",
        "기간(최근 N주)은 기존 규칙대로 수율 측정일 기준이다",
        "사용자가 OPER_ID나 공정 설명을 주면 이 테이블에서 찾는다",
        "DF_LEGEND_LONG과 DF_FDC_ALARM은 장비 이력 용도로 쓰지 않으며",
        "DF_LEGEND_LONG을 대체로 쓰지 않는다",
        "\ud14c\uc2a4\ud2b8\uc6a9 \ub354\ubbf8",
    ):
        if phrase == "\ud14c\uc2a4\ud2b8\uc6a9 \ub354\ubbf8":
            assert phrase not in notes
        else:
            assert phrase in notes


def test_wafer_membership_rendering_contract():
    root = Path(__file__).resolve().parents[1] / 'instructions'
    for name in ('native.md', 'domain-notes.md'):
        text = (root / name).read_text()
        for required in ('member_grain:"wafer"', 'group_id,lot_id,wf_id', 'PRB WFID 원문 문자열',
                         'query_db/run_python', 'render_yield_table', '임의 중복 제거 없이 clarify'):
            assert required in text
