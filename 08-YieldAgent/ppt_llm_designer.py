"""
PPT LLM Designer
=================
GLM-4.7 (OpenRouter 경유) 을 사용하여 수율 분석 PPT의
슬라이드 구조 및 디자인을 JSON으로 생성합니다.

파이프라인: raw data → LLM → PresentationDesign(JSON) → ppt_renderer
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from json_repair import repair_json
from pydantic import BaseModel, Field

from common import get_llm

logger = logging.getLogger("yield_agent.ppt_llm_designer")

PPT_DESIGN_MODEL = "glm-4.7"

# ── Pydantic 스키마 ──────────────────────────────────────────

class ColorScheme(BaseModel):
    primary: str = Field(description="메인 색상 hex (예: '#1E293B')")
    accent: str = Field(description="강조 색상 hex")
    background: str = Field(description="슬라이드 배경 hex")
    text: str = Field(description="본문 텍스트 hex")
    success: str = Field(description="개선/양호 표시 hex (녹색 계열)")
    danger: str = Field(description="열화/경고 표시 hex (빨강 계열)")
    table_header: str = Field(description="테이블 헤더 배경 hex")
    table_alt_row: str = Field(description="테이블 짝수행 배경 hex")


class ElementStyle(BaseModel):
    font_size: float | None = Field(default=None, description="폰트 크기 (pt)")
    font_color: str | None = Field(default=None, description="폰트 색상 hex")
    fill_color: str | None = Field(default=None, description="배경 색상 hex")
    bold: bool = Field(default=False)


class ElementDesign(BaseModel):
    type: str = Field(description="table | chart | textbox | shape")
    data_ref: str = Field(description="데이터 참조 키")
    left: float = Field(description="왼쪽 위치 (inches)")
    top: float = Field(description="위쪽 위치 (inches)")
    width: float = Field(description="너비 (inches)")
    height: float = Field(description="높이 (inches)")
    style: ElementStyle | None = Field(default=None)

    @property
    def resolved_style(self) -> ElementStyle:
        return self.style or ElementStyle()


class SlideDesign(BaseModel):
    title: str = Field(description="슬라이드 제목")
    layout: str = Field(description="title | data_overview | chart_focus | analysis | ending")
    background_color: str | None = Field(default=None, description="슬라이드 배경 hex (None이면 흰색)")
    elements: list[ElementDesign] = Field(default_factory=list)
    extra_content: str = Field(default="", description="LLM 생성 요약 텍스트 (extra_content data_ref용)", exclude=True)


class PresentationDesign(BaseModel):
    color_scheme: ColorScheme
    font_family: str = Field(description="폰트 이름 (예: '맑은 고딕')")
    slides: list[SlideDesign]


# ── 사용 가능한 data_ref 목록 ────────────────────────────────

AVAILABLE_DATA_REFS = """
사용 가능한 data_ref 목록 (elements에서 참조):
- "unified_table": PT1H/PT1C/GMS 통합 멀티헤더 테이블 (type: table)
- "scatter_pt1h_pt1c": PT1H+PT1C 이상 파라미터 트렌드 scatter plot (type: chart)
- "scatter_degradation": 열화/개선 파라미터 트렌드 scatter plot (type: chart)
- "scatter_gms": GMS 수율 트렌드 scatter plot (type: chart)
- "title_text": 리포트 제목 텍스트 (type: textbox)
- "subtitle_text": 부제목 (기준일, 단위 등) (type: textbox)
- "ending_text": 마무리 텍스트 (type: textbox)
- "ending_footer": 생성 정보 footer (type: textbox)
- "anomaly_summary": 이상 파라미터 요약 텍스트 (type: textbox)
- "extra_content": LLM이 생성한 추가 섹션 요약 텍스트 (type: textbox)
"""

# ── 시스템 프롬프트 ──────────────────────────────────────────

SYSTEM_PROMPT = f"""당신은 반도체 수율 분석 PPT 디자인 전문가입니다.
16:9 슬라이드 (13.33 x 7.5 inches) 에 데이터 테이블과 scatter plot 차트를 효과적으로 배치하는 것이 목표입니다.

## 규칙
1. 슬라이드는 정확히 3장 구성: title(타이틀) → data_overview(데이터+차트) → ending(마무리)
2. 색상은 전문적이고 가독성 높은 팔레트를 선택하라
3. data_overview 슬라이드에는 반드시 unified_table(상단) + 3개 scatter chart(하단) 배치
4. 모든 좌표는 inches 단위, 슬라이드 범위 내(0~13.33 x 0~7.5)
5. 요소가 겹치지 않도록 배치하라
6. 테이블은 상단 절반(y: 1.1~3.8), 차트는 하단 절반(y: 3.9~7.2)에 배치

{AVAILABLE_DATA_REFS}

## 출력
반드시 JSON만 출력하라. 설명 텍스트 없이 순수 JSON만.
"""


def _build_user_prompt(state: dict[str, Any]) -> str:
    """state에서 LLM에게 전달할 요약 정보를 구성."""
    lotcd = state.get("lotcd", "Unknown")
    ref_date = state.get("ref_date", "")
    unit = state.get("unit", "weekly")
    weeks_data = state.get("weeks_data", [])
    anomaly_params = state.get("anomaly_params", [])

    unit_label = {"weekly": "주간", "monthly": "월간", "daily": "일별"}.get(unit, unit)
    n_weeks = len(weeks_data)

    anomaly_summary = "없음"
    if anomaly_params:
        lines = []
        for a in anomaly_params:
            pct = a.get("change_pct", 0)
            lines.append(f"  - {a['param']}: {a.get('direction', '?')} ({pct:+.1f}%)")
        anomaly_summary = "\n".join(lines)

    return f"""다음 수율 분석 데이터에 맞는 PPT 디자인을 JSON으로 생성해주세요.

## 데이터 정보
- 제품 코드: {lotcd}
- 기준일: {ref_date}
- 분석 단위: {unit_label}
- 데이터 기간 수: {n_weeks}개
- 이상 감지 파라미터:
{anomaly_summary}

## 요구사항
- 3장 슬라이드: title → data_overview → ending
- title 슬라이드: 진한 배경 + 흰색 제목/부제목
- data_overview 슬라이드: 상단에 unified_table, 하단에 scatter 3개 (scatter_pt1h_pt1c, scatter_degradation, scatter_gms) 가로 배치
- ending 슬라이드: 진한 배경 + Thank You + footer
- 이상 파라미터가 {len(anomaly_params)}개 감지되었으므로 열화 강조에 적합한 danger 색상 선택
- 전문적이고 세련된 색상 팔레트 사용

PresentationDesign JSON 스키마:
{PresentationDesign.model_json_schema()}
"""


def generate_slide_design(state: dict[str, Any]) -> PresentationDesign:
    """GLM-4.7로 슬라이드 디자인 JSON을 생성하고 PresentationDesign으로 파싱."""
    llm = get_llm(model=PPT_DESIGN_MODEL, temperature=0.7)
    user_prompt = _build_user_prompt(state)

    logger.info("[PPT Designer] GLM-4.7 호출 시작")
    response = llm.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ])
    raw_text = response.content or ""
    logger.debug("[PPT Designer] LLM 응답: %s", raw_text[:500])

    # JSON 추출 (```json ... ``` 또는 { ... })
    clean = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean, re.DOTALL)
    if not json_match:
        json_match = re.search(r"(\{.*\})", clean, re.DOTALL)
    if not json_match:
        json_match = re.search(r"(\{.*\})", raw_text, re.DOTALL)
    if not json_match:
        logger.warning("[PPT Designer] JSON 파싱 실패, 기본 디자인 사용")
        return _default_design(state)

    json_str = json_match.group(1)
    try:
        data = json.loads(repair_json(json_str))
        design = PresentationDesign(**data)
        logger.info("[PPT Designer] 디자인 생성 완료: %d slides", len(design.slides))
        return design
    except Exception as e:
        logger.warning("[PPT Designer] 파싱 실패 (%s), 기본 디자인 사용", e)
        return _default_design(state)


# ── 추가 섹션별 슬라이드 생성 ────────────────────────────────

_EXTRA_SECTION_CONFIG = {
    "wads": {
        "title": "WADS 이상 탐지 요약",
        "data_key": "wads_artifacts",
        "prompt": "WADS(열화 탐지) 분석 결과를 요약하세요. 핵심 발견, 파라미터 상태(Critical/Warning/Normal), 권장 조치를 포함하세요.",
    },
    "fail_history": {
        "title": "불량 이력 요약",
        "data_key": "fail_history_artifacts",
        "prompt": "불량이력(Fail History) 검색 결과를 요약하세요. 주요 불량 유형, 발생 빈도, 최근 추세, 조치 현황을 포함하세요.",
    },
    "analysis": {
        "title": "AI 종합 분석",
        "data_key": "analysis_result",
        "prompt": "AI 분석 결과를 요약하세요. 핵심 발견, 원인 추정, 권장 조치를 구조화하세요.",
    },
}


def _extract_text_from_artifacts(artifacts: list[dict]) -> str:
    """artifact 리스트에서 텍스트 콘텐츠를 추출 (HTML 태그 제거)."""
    import re as _re
    texts = []
    for art in artifacts:
        data = art.get("data", "")
        if not data:
            continue
        # HTML 태그 제거하여 텍스트만 추출
        clean = _re.sub(r"<[^>]+>", " ", data)
        clean = _re.sub(r"\s+", " ", clean).strip()
        if clean:
            texts.append(clean[:1000])  # 각 artifact 최대 1000자
    return "\n".join(texts[:5])  # 최대 5개


def generate_extra_slide(state: dict[str, Any], section_type: str,
                         color_scheme: dict | None = None) -> SlideDesign:
    """추가 섹션(WADS/불량이력/AI분석)의 1페이지 슬라이드를 LLM으로 생성.

    LLM에게 데이터를 요약시킨 뒤 textbox로 배치한 SlideDesign을 반환.
    LLM 실패 시 기본 레이아웃으로 fallback.
    """
    config = _EXTRA_SECTION_CONFIG.get(section_type)
    if not config:
        return _default_extra_slide(section_type, "지원하지 않는 섹션입니다.")

    # 데이터 추출
    raw_data = state.get(config["data_key"], "")
    if isinstance(raw_data, list):
        content = _extract_text_from_artifacts(raw_data)
    else:
        content = str(raw_data)[:3000]

    if not content.strip():
        return _default_extra_slide(section_type, "데이터가 없습니다.")

    # LLM으로 요약 생성
    try:
        llm = get_llm(model=PPT_DESIGN_MODEL, temperature=0.3)
        summary_prompt = f"""{config['prompt']}

## 원본 데이터
{content}

## 규칙
- 한국어로 작성
- 마크다운 없이 순수 텍스트로
- 핵심 내용을 구조화하여 3~5개 항목으로 정리
- 각 항목은 줄바꿈으로 구분
- 최대 500자 이내"""

        response = llm.invoke([
            {"role": "system", "content": "반도체 수율 분석 전문가입니다. PPT 슬라이드에 넣을 요약문을 작성하세요."},
            {"role": "user", "content": summary_prompt},
        ])
        summary_text = (response.content or "").strip()
        # <think> 태그 제거
        summary_text = re.sub(r"<think>.*?</think>", "", summary_text, flags=re.DOTALL).strip()
        if not summary_text:
            raise ValueError("빈 응답")
        logger.info("[PPT Designer] %s 요약 생성 완료 (%d자)", section_type, len(summary_text))
    except Exception as e:
        logger.warning("[PPT Designer] %s 요약 생성 실패 (%s), 원본 텍스트 사용", section_type, e)
        # fallback: 원본 텍스트 앞부분 사용
        summary_text = content[:500]

    return _default_extra_slide(section_type, summary_text, config["title"])


def _default_extra_slide(section_type: str, content: str,
                         title: str | None = None) -> SlideDesign:
    """추가 섹션의 기본 레이아웃 SlideDesign."""
    section_titles = {
        "wads": "WADS 이상 탐지 요약",
        "fail_history": "불량 이력 요약",
        "analysis": "AI 종합 분석",
    }
    slide_title = title or section_titles.get(section_type, section_type)

    return SlideDesign(
        title=slide_title,
        layout="data_overview",
        elements=[
            ElementDesign(type="textbox", data_ref="extra_content",
                          left=0.8, top=1.2, width=11.7, height=5.8,
                          style=ElementStyle(font_size=14, font_color="#1E293B")),
        ],
        extra_content=content,
    )


def _default_design(state: dict[str, Any]) -> PresentationDesign:
    """LLM 호출 실패 시 사용하는 기본 디자인."""
    lotcd = state.get("lotcd", "Unknown")
    ref_date = state.get("ref_date", "")
    unit = state.get("unit", "weekly")
    unit_label = {"weekly": "주간", "monthly": "월간", "daily": "일별"}.get(unit, unit)

    return PresentationDesign(
        color_scheme=ColorScheme(
            primary="#1E293B",
            accent="#3B82F6",
            background="#FFFFFF",
            text="#1E293B",
            success="#22C55E",
            danger="#EF4444",
            table_header="#1E293B",
            table_alt_row="#F1F5F9",
        ),
        font_family="맑은 고딕",
        slides=[
            SlideDesign(
                title=f"{lotcd} 수율 분석 리포트",
                layout="title",
                background_color="#1E293B",
                elements=[
                    ElementDesign(type="textbox", data_ref="title_text",
                                  left=1.0, top=2.2, width=11.33, height=1.2,
                                  style=ElementStyle(font_size=40, font_color="#FFFFFF", bold=True)),
                    ElementDesign(type="textbox", data_ref="subtitle_text",
                                  left=1.0, top=3.5, width=11.33, height=0.6,
                                  style=ElementStyle(font_size=16, font_color="#CBD5E1")),
                ],
            ),
            SlideDesign(
                title=f"{lotcd} 수율 요약",
                layout="data_overview",
                elements=[
                    ElementDesign(type="table", data_ref="unified_table",
                                  left=0.2, top=1.15, width=12.9, height=2.5),
                    ElementDesign(type="chart", data_ref="scatter_pt1h_pt1c",
                                  left=0.2, top=3.9, width=4.2, height=3.3),
                    ElementDesign(type="chart", data_ref="scatter_degradation",
                                  left=4.5, top=3.9, width=4.2, height=3.3),
                    ElementDesign(type="chart", data_ref="scatter_gms",
                                  left=8.8, top=3.9, width=4.2, height=3.3),
                ],
            ),
            SlideDesign(
                title="Thank You",
                layout="ending",
                background_color="#1E293B",
                elements=[
                    ElementDesign(type="textbox", data_ref="ending_text",
                                  left=1.0, top=2.5, width=11.33, height=1.0,
                                  style=ElementStyle(font_size=36, font_color="#FFFFFF", bold=True)),
                    ElementDesign(type="textbox", data_ref="ending_footer",
                                  left=1.0, top=3.8, width=11.33, height=0.5,
                                  style=ElementStyle(font_size=14, font_color="#CBD5E1")),
                ],
            ),
        ],
    )
