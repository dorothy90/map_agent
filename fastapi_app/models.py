from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


LotCd = Literal["4SS"]
Unit = Literal["weekly"]
Process = Literal["pt1h", "pt1c", "gms"]


class YieldAvgRequest(BaseModel):
    """POST /yieldAvg 요청 바디"""
    lotcd: str = "4SS"
    unit: str = "weekly"
    process: str = "pt1h"
    date: str = Field(..., description="YYYYMMDD")


class Pt1hPara(BaseModel):
    # 대표적인 반도체(wafer sort / parametric test) 측정 항목 예시 25개
    VTH: float
    IDSAT: float
    IDLIN: float
    IOFF: float
    ION: float
    IGATE: float
    IDDQ: float
    VMIN: float
    FMAX: float
    TPD: float
    GM_MAX: float
    SS: float
    DIBL: float
    RON: float
    RDS_ON: float
    BVDS: float
    LEAK_ID: float
    LEAK_IG: float
    CGB: float
    CGS: float
    CGD: float
    CDS: float
    RSH: float
    RD: float
    RS: float


class Pt1cPara(BaseModel):
    """pt1c 파라미터 (pt1h와 동일 구조)"""
    VTH: float
    IDSAT: float
    IDLIN: float
    IOFF: float
    ION: float
    IGATE: float
    IDDQ: float
    VMIN: float
    FMAX: float
    TPD: float
    GM_MAX: float
    SS: float
    DIBL: float
    RON: float
    RDS_ON: float
    BVDS: float
    LEAK_ID: float
    LEAK_IG: float
    CGB: float
    CGS: float
    CGD: float
    CDS: float
    RSH: float
    RD: float
    RS: float


class GmsPara(BaseModel):
    """GMS 파라미터"""
    cum0: float
    cum2: float
    fab: float
    prb: float
    pnt: float


class WeeklyRecordPt1h(BaseModel):
    lotcd: LotCd
    unit: Unit
    process: Literal["pt1h"]
    week: str = Field(..., description="ISO week string, e.g. 2026-W06")
    week_start: date
    week_end: date
    lotcount: int
    wfCount: int
    pt1hPara: Pt1hPara


class WeeklyRecordPt1c(BaseModel):
    lotcd: LotCd
    unit: Unit
    process: Literal["pt1c"]
    week: str = Field(..., description="ISO week string, e.g. 2026-W06")
    week_start: date
    week_end: date
    lotcount: int
    wfCount: int
    pt1cPara: Pt1cPara


class WeeklyRecordGms(BaseModel):
    lotcd: LotCd
    unit: Unit
    process: Literal["gms"]
    week: str = Field(..., description="ISO week string, e.g. 2026-W06")
    week_start: date
    week_end: date
    lotcount: int
    wfCount: int
    gmsPara: GmsPara


# 하위 호환용 (pt1h 전용)
WeeklyRecord = WeeklyRecordPt1h


class WeeklyFlatResponse(BaseModel):
    lotcount: int
    wfCount: int
    VTH: float | int
    IDSAT: float | int
    IDLIN: float | int
    IOFF: float | int
    ION: float | int
    IGATE: float | int
    IDDQ: float | int
    VMIN: float | int
    FMAX: float | int
    TPD: float | int
    GM_MAX: float | int
    SS: float | int
    DIBL: float | int
    RON: float | int
    RDS_ON: float | int
    BVDS: float | int
    LEAK_ID: float | int
    LEAK_IG: float | int
    CGB: float | int
    CGS: float | int
    CGD: float | int
    CDS: float | int
    RSH: float | int
    RD: float | int
    RS: float | int


class GmsFlatResponse(BaseModel):
    lotcount: int
    wfCount: int
    cum0: float | int
    cum2: float | int
    fab: float | int
    prb: float | int
    pnt: float | int

