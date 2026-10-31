"""Shared input contracts for immutable wafer-table tools."""
from __future__ import annotations

from datetime import datetime
import math
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from .types import Contract


ColumnName = str
Scalar = str | int | float | bool | None


class TableHandle(Contract):
    result_id: str = Field(min_length=1, description="저장 결과 식별자")
    table_id: str = Field(min_length=1, description="저장 표 식별자")
    version: str | None = Field(default=None, description="표 행 내용의 선택적 버전")


class TimeRange(Contract):
    column: str = Field(min_length=1, max_length=128, description="기간 조건을 적용할 열")
    start: datetime = Field(description="포함되는 시작 시각")
    end: datetime = Field(description="포함되지 않는 종료 시각")
    time_zone: Literal["Asia/Seoul"] = Field(default="Asia/Seoul", description="시각 해석 기준")

    @model_validator(mode="after")
    def ordered(self):
        for name in ("start", "end"):
            value = getattr(self, name)
            if value.tzinfo is not None:
                setattr(self, name, value.astimezone(ZoneInfo(self.time_zone)).replace(tzinfo=None))
        if self.end <= self.start:
            raise ValueError("end must follow start")
        return self


class WaferPair(Contract):
    lot_id: str = Field(min_length=1, max_length=100, description="멤버의 첫 번째 식별자")
    wf_id: int = Field(ge=0, description="멤버의 두 번째 식별자")


class PredicateAtom(Contract):
    column: str = Field(min_length=1, max_length=128, description="조건을 적용할 열")
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "in", "not_in", "between",
                "is_null", "not_null", "mod_eq"] = Field(description="조건 연산")
    value: Any = Field(default=None, description="조건과 비교할 값")
    divisor: int | None = Field(default=None, ge=1, le=1000, description="나머지 연산의 양의 제수")
    remainder: int | None = Field(default=None, ge=0, description="나머지 연산의 결과")

    @model_validator(mode="after")
    def valid_shape(self):
        if self.op in ("is_null", "not_null"):
            if self.value is not None or self.divisor is not None or self.remainder is not None:
                raise ValueError("null predicates do not accept values")
        elif self.op in ("in", "not_in"):
            if not isinstance(self.value, list) or not 1 <= len(self.value) <= 500:
                raise ValueError("set predicate value must contain 1..500 items")
        elif self.op == "between":
            if not isinstance(self.value, list) or len(self.value) != 2 or self.value[1] < self.value[0]:
                raise ValueError("between requires an ordered pair")
        elif self.op == "mod_eq":
            if self.value is not None or self.divisor is None or self.remainder is None:
                raise ValueError("mod_eq requires divisor and remainder")
            if self.remainder >= self.divisor:
                raise ValueError("remainder must be less than divisor")
        elif self.value is None or isinstance(self.value, (list, dict)):
            raise ValueError("comparison predicate requires a scalar value")
        values = self.value if isinstance(self.value, list) else [self.value]
        if any(isinstance(value, float) and not math.isfinite(value) for value in values):
            raise ValueError("predicate values must be finite")
        return self


class PredicateGroup(Contract):
    mode: Literal["all", "any"] = Field(default="all", description="조건 결합 방식")
    items: list[PredicateAtom] = Field(min_length=1, max_length=32, description="결합할 단일 조건")


class SourceQuery(Contract):
    lot_cd: list[str] = Field(default_factory=list, max_length=20, description="제품 식별자 조건")
    process: list[str] = Field(default_factory=list, max_length=20, description="공정 식별자 조건")
    lot_ids: list[str] = Field(default_factory=list, max_length=500, description="멤버 식별자 조건")
    wafer_pairs: list[WaferPair] = Field(default_factory=list, max_length=1000,
                                         description="정확한 멤버 식별자 쌍")
    time: TimeRange | None = Field(default=None, description="선택적 반개구간 시각 조건")
    where: PredicateGroup | None = Field(default=None, description="허용된 열에 적용할 추가 조건")

    @model_validator(mode="after")
    def exclusive_identity(self):
        if self.lot_ids and self.wafer_pairs:
            raise ValueError("lot_ids and wafer_pairs cannot be combined")
        pairs = [(item.lot_id, item.wf_id) for item in self.wafer_pairs]
        if len(pairs) != len(set(pairs)):
            raise ValueError("wafer_pairs must be unique")
        return self


class ArtifactSelection(Contract):
    result_id: str = Field(min_length=1, description="저장 결과 식별자")
    artifact_ids: list[str] = Field(min_length=1, max_length=30, description="선택할 파일 식별자")

