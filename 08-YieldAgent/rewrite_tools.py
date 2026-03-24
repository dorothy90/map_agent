"""
Rewrite Tools — rewrite_node에서 사용하는 도구 모음
=================================================
LLM이 자연어 패턴(N배수, 홀수, 짝수 등)을 인식하면
도구를 호출하여 정확한 wafer ID 목록을 계산합니다.
"""

from langchain_core.tools import tool

_MAX_WF_ID = 25  # 반도체 fab 표준 wafer 범위 (1~25)


@tool
def compute_wafer_ids(
    pattern_type: str,
    value: int = 0,
    start: int = 1,
    end: int = 25,
) -> str:
    """wafer ID 패턴을 계산하여 쉼표 구분 ID 목록을 반환합니다.

    Args:
        pattern_type: 패턴 유형. 다음 중 하나:
            - "multiple": N의 배수 (value=N). 예: "3배수" → pattern_type="multiple", value=3
            - "odd": 홀수 wafer. 예: "홀수 wafer"
            - "even": 짝수 wafer. 예: "짝수 wafer"
            - "range": start~end 범위. 예: "1~10번 wafer" → start=1, end=10
            - "first_n": 처음 N개 (value=N). 예: "처음 5개 wafer"
            - "last_n": 마지막 N개 (value=N). 예: "마지막 3개 wafer"
        value: multiple/first_n/last_n에서 사용할 숫자 (기본 0)
        start: 범위 시작 (기본 1)
        end: 범위 끝 (기본 25)

    Returns:
        쉼표 구분 wafer ID 문자열. 예: "03,06,09,12,15,18,21,24"
    """
    ids: list[int] = []

    if pattern_type == "multiple" and value > 0:
        ids = list(range(value, _MAX_WF_ID + 1, value))
    elif pattern_type == "odd":
        ids = [i for i in range(1, _MAX_WF_ID + 1) if i % 2 == 1]
    elif pattern_type == "even":
        ids = [i for i in range(1, _MAX_WF_ID + 1) if i % 2 == 0]
    elif pattern_type == "range":
        s = max(1, start)
        e = min(_MAX_WF_ID, end)
        ids = list(range(s, e + 1))
    elif pattern_type == "first_n" and value > 0:
        ids = list(range(1, min(value, _MAX_WF_ID) + 1))
    elif pattern_type == "last_n" and value > 0:
        ids = list(range(max(1, _MAX_WF_ID - value + 1), _MAX_WF_ID + 1))

    if not ids:
        return "패턴을 인식하지 못했습니다. pattern_type을 확인해주세요."

    return ",".join(f"{i:02d}" for i in ids)


REWRITE_TOOLS = [compute_wafer_ids]
