from datetime import datetime, time
from pathlib import Path


def load_domain_notes() -> str:
    return (Path(__file__).with_name("instructions") / "domain-notes.md").read_text(encoding="utf-8").strip()


def load_behavior_notes() -> str:
    return (Path(__file__).with_name("instructions") / "behavior.md").read_text(encoding="utf-8").strip()


def web_turn_instructions(request_context, *, code_enabled=True, kanban_enabled=True) -> str:
    requested_at = request_context['requested_at']
    local_time = datetime.fromisoformat(requested_at).replace(tzinfo=None)
    day_start = datetime.combine(local_time.date(), time.min)
    notes = load_domain_notes()
    behavior = load_behavior_notes()
    python_notes = (
        "execute_code에서 query_db가 반환한 전체 CSV 경로를 pd.read_csv(path)로 읽는다. 다른 저장 표는 read_result(result_id, table_id, export_for_python=true)로 전체 CSV 경로를 얻는다. 계산 변수와 DataFrame은 같은 채팅의 다음 호출에서도 이어서 쓸 수 있다. emit_table과 emit_plot은 표와 차트를 채팅에 표시한다. 다운로드할 완성 파일은 셀마다 제공되는 publish_directory(Path)에 저장한다. 각 파일은 도구 실행이 끝날 때 현재 채팅에 등록되며, artifact_refs의 media를 그대로 한 줄에 쓰면 다운로드 버튼이 표시된다. "
        "최종 답변에는 요청한 결과와 다운로드 링크를 간결하게 제공한다."
    ) if code_enabled else ""
    board_notes = (
        "직원의 불량 아이템은 채팅 간에 유지되는 개인 칸반 카드다. 카드에 등록하거나 추가하는 "
        "웨이퍼의 식별 키는 MAP.LOT_ID와 MAP.WF_ID 쌍이다. 사용자가 지정하거나 맵에서 선택한 "
        "쌍을 wafer_groups에 그대로 전달한다. PRB의 ALIAS_LOT_ID와 스플릿 이력은 별개다. 제품과 맵 검사명이 확인된 "
        "경우 함께 등록한다. 이미 등록한 아이템은 보드에서 찾아 같은 카드에 추가한다. "
        "저장된 아이템 전체의 맵 조회에는 카드 ID를 load_wafer_maps의 wafer_item_id로 전달한다."
    ) if kanban_enabled else ""
    return (
        f"{notes}\n\n{behavior}\n\n{python_notes}\n\n{board_notes}\n\n## 현재 요청의 조회 기준\n"
        f"요청 시각: {requested_at} ({request_context['timezone']})\n"
        "실적 조회의 기본 마감은 요청 시각이다. 별도 기간이나 마감을 지정하면 그 범위를 따른다. "
        "당일 누적 실적에 사용할 현지 DB 시각 범위는 "
        f">= {day_start.isoformat()} 및 <= {local_time.isoformat()} 이다. "
        "제품과 측정 시각 조건을 원본 조회에 적용한 뒤 해당 범위의 결과로 집계한다."
    )
