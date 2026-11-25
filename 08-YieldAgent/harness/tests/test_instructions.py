from pathlib import Path

import pytest

from harness.instructions import load_domain_notes, web_turn_instructions


def test_turn_instructions_contain_domain_notes_once_independent_of_cwd(monkeypatch, tmp_path):
    notes = load_domain_notes()
    monkeypatch.chdir(tmp_path)
    prompt = web_turn_instructions({'requested_at': '2026-11-01T12:10:05+09:00', 'timezone': 'Asia/Seoul'})
    assert prompt.count(notes) == 1
    assert "lot은 LOT_ID를 뜻하며, LOT_CD는 제품 코드다." in notes
    assert "DF_DIE_TO_WF_YLD는 더 이상 사용하지 않으므로 조회하지 않는다." in notes
    assert "item PARAM(VTH, FMAX 등)은 테스트 측정값" in notes
    assert "동일시점 팹아웃(사용자 확인, 2026-11-15): 동일한 PT 측정일 범위를 뜻하며 실제 FABOUT 날짜 범위와는 구분한다." in notes


def test_missing_domain_notes_raises(monkeypatch):
    original = Path.read_text

    def missing_notes(path, *args, **kwargs):
        if path.name == 'domain-notes.md':
            raise FileNotFoundError(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', missing_notes)
    with pytest.raises(FileNotFoundError):
        load_domain_notes()


def test_domain_notes_describe_wf_legend_join_and_forbid_substitutes():
    notes = load_domain_notes()
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
        "LOT_ID 및 WF_ID=WFID로 조인해 query_db와 Python으로 멤버십을 조회·구성한다",
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
    notes = load_domain_notes()
    for required in ('member_grain:"wafer"', 'group_id,lot_id,wf_id', 'PRB WFID 원문 문자열',
                     'query_db', 'render_yield_table', '임의 중복 제거 없이 clarify'):
        assert required in notes


@pytest.mark.parametrize('code_enabled,kanban_enabled', [(True, True), (True, False), (False, True), (False, False)])
def test_turn_instructions_include_behavior_once(monkeypatch, tmp_path, code_enabled, kanban_enabled):
    from harness import instructions

    monkeypatch.chdir(tmp_path)
    prompt = instructions.web_turn_instructions(
        {'requested_at': '2026-11-01T12:10:05+09:00', 'timezone': 'Asia/Seoul'},
        code_enabled=code_enabled, kanban_enabled=kanban_enabled,
    )
    behavior = instructions.load_behavior_notes()
    assert behavior
    assert prompt.count(behavior) == 1
    assert prompt.count(load_domain_notes()) == 1
    # These are model-facing policy contracts, not Python implementation details.
    for rule in (
        '이번 대화 기준:', '사용자의 원래 요청과 최신 정정', '다시 묻지 않는다',
        'assistant의 추측', 'session_search', '미확정 후보',
        '확인 전에는', 'load_wafer_maps', 'render_yield_table', 'query_db',
        '추천(첫 번째) 선택지', '세션별 조건 묶음', '서로 다른 세션의 조건을 합치지 않는다',
        '누락된 필수 조건만', '하나의 clarify 카드', '실제 DB의 distinct 값',
        '저수율', '그룹 소속', '반드시 clarify', '평균±시그마', '2개 그룹',
        'timed_out', 'cancelled', '턴을 종료한다',
        '요청된 스킬을 먼저 읽고', '여러 테스트의 기본 범위', '많다',
        'REPORT_PRODUCT_REQUIRED', 'lot_cd', '오류 호출을 반복하지 않는다',
    ):
        assert rule in behavior
    assert '테스트용 더미' not in behavior


def test_missing_behavior_notes_raises(monkeypatch):
    original = Path.read_text

    def missing_behavior(path, *args, **kwargs):
        if path.name == 'behavior.md':
            raise FileNotFoundError(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', missing_behavior)
    with pytest.raises(FileNotFoundError):
        web_turn_instructions({'requested_at': '2026-11-01T12:10:05+09:00', 'timezone': 'Asia/Seoul'})


def test_behavior_is_reloaded_between_turns(monkeypatch):
    original = Path.read_text
    versions = iter(('behavior v1', 'behavior v2'))

    def changing_behavior(path, *args, **kwargs):
        if path.name == 'behavior.md':
            return next(versions)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', changing_behavior)
    context = {'requested_at': '2026-11-01T12:10:05+09:00', 'timezone': 'Asia/Seoul'}
    assert 'behavior v1' in web_turn_instructions(context)
    second = web_turn_instructions(context)
    assert 'behavior v2' in second
    assert 'behavior v1' not in second
