from __future__ import annotations

from datetime import date, datetime

from fastapi import FastAPI, HTTPException, Query

try:
    # 정상 실행(패키지로 실행): uvicorn fastapi_app.main:app
    from .models import GmsFlatResponse, WeeklyFlatResponse, YieldAvgRequest
    from .storage import get_store
except ImportError:  # pragma: no cover
    # 파일 직접 실행: python fastapi_app/main.py
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fastapi_app.models import GmsFlatResponse, WeeklyFlatResponse, YieldAvgRequest  # type: ignore[no-redef]
    from fastapi_app.storage import get_store  # type: ignore[no-redef]

app = FastAPI(title="Weekly metrics API (pt1h/pt1c/gms)", version="0.2.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _parse_yyyymmdd(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError as e:
        raise HTTPException(status_code=400, detail="date는 YYYYMMDD 형식이어야 합니다.") from e


def _to_number(x: float) -> float | int:
    # JSON에서 9.0 대신 9로 떨어지도록 정수형은 int로 변환
    if isinstance(x, int):
        return x
    if float(x).is_integer():
        return int(x)
    return x


def _pt1h_pt1c_response(rec, para_key: str):
    para = getattr(rec, para_key)
    flat = {k: _to_number(v) for k, v in para.model_dump().items()}
    return WeeklyFlatResponse(lotcount=rec.lotcount, wfCount=rec.wfCount, **flat)


SUPPORTED_PROCESSES = ("pt1h", "pt1c", "gms")


@app.post("/yieldAvg")
def yield_avg(req: YieldAvgRequest):
    """POST로 lotcd, unit, process, date를 받아 수율 데이터 반환 (pt1h/pt1c/gms 통합)"""
    if req.lotcd != "4SS":
        raise HTTPException(status_code=400, detail="lotcd는 '4SS'만 지원합니다.")
    if req.unit != "weekly":
        raise HTTPException(status_code=400, detail="unit은 'weekly'만 지원합니다.")
    if req.process not in SUPPORTED_PROCESSES:
        raise HTTPException(
            status_code=400,
            detail=f"process는 {', '.join(SUPPORTED_PROCESSES)} 중 하나여야 합니다.",
        )

    d = _parse_yyyymmdd(req.date)
    store = get_store(req.process)
    rec = store.get_week(d)

    if req.process == "gms":
        para = {k: _to_number(v) for k, v in rec.gmsPara.model_dump().items()}
        return GmsFlatResponse(lotcount=rec.lotcount, wfCount=rec.wfCount, **para)
    para_key = "pt1hPara" if req.process == "pt1h" else "pt1cPara"
    return _pt1h_pt1c_response(rec, para_key)


@app.get("/weekly")
def weekly_unified(
    lotcd: str = Query(..., description="예: 4SS"),
    unit: str = Query(..., description="예: weekly"),
    process: str = Query(..., description="pt1h, pt1c, gms 중 하나"),
    date_str: str = Query(..., alias="date", description="YYYYMMDD"),
):
    """process 파라미터에 따라 pt1h/pt1c/gms 데이터 반환"""
    if lotcd != "4SS":
        raise HTTPException(status_code=400, detail="lotcd는 '4SS'만 지원합니다.")
    if unit != "weekly":
        raise HTTPException(status_code=400, detail="unit은 'weekly'만 지원합니다.")
    if process not in SUPPORTED_PROCESSES:
        raise HTTPException(
            status_code=400,
            detail=f"process는 {', '.join(SUPPORTED_PROCESSES)} 중 하나여야 합니다.",
        )

    d = _parse_yyyymmdd(date_str)
    store = get_store(process)
    rec = store.get_week(d)

    if process == "gms":
        para = {k: _to_number(v) for k, v in rec.gmsPara.model_dump().items()}
        return GmsFlatResponse(lotcount=rec.lotcount, wfCount=rec.wfCount, **para)
    para_key = "pt1hPara" if process == "pt1h" else "pt1cPara"
    return _pt1h_pt1c_response(rec, para_key)


@app.get("/pt1h/weekly", response_model=WeeklyFlatResponse)
def pt1h_weekly(
    lotcd: str = Query(..., description="예: 4SS"),
    unit: str = Query(..., description="예: weekly"),
    process: str = Query(..., description="예: pt1h"),
    date_str: str = Query(..., alias="date", description="YYYYMMDD"),
) -> WeeklyFlatResponse:
    if lotcd != "4SS":
        raise HTTPException(status_code=400, detail="lotcd는 '4SS'만 지원합니다.")
    if unit != "weekly":
        raise HTTPException(status_code=400, detail="unit은 'weekly'만 지원합니다.")
    if process != "pt1h":
        raise HTTPException(status_code=400, detail="process는 'pt1h'만 지원합니다.")

    d = _parse_yyyymmdd(date_str)
    store = get_store("pt1h")
    rec = store.get_week(d)
    return _pt1h_pt1c_response(rec, "pt1hPara")


@app.get("/pt1c/weekly", response_model=WeeklyFlatResponse)
def pt1c_weekly(
    lotcd: str = Query(..., description="예: 4SS"),
    unit: str = Query(..., description="예: weekly"),
    process: str = Query(..., description="예: pt1c"),
    date_str: str = Query(..., alias="date", description="YYYYMMDD"),
) -> WeeklyFlatResponse:
    if lotcd != "4SS":
        raise HTTPException(status_code=400, detail="lotcd는 '4SS'만 지원합니다.")
    if unit != "weekly":
        raise HTTPException(status_code=400, detail="unit은 'weekly'만 지원합니다.")
    if process != "pt1c":
        raise HTTPException(status_code=400, detail="process는 'pt1c'만 지원합니다.")

    d = _parse_yyyymmdd(date_str)
    store = get_store("pt1c")
    rec = store.get_week(d)
    return _pt1h_pt1c_response(rec, "pt1cPara")


@app.get("/gms/weekly", response_model=GmsFlatResponse)
def gms_weekly(
    lotcd: str = Query(..., description="예: 4SS"),
    unit: str = Query(..., description="예: weekly"),
    process: str = Query(..., description="예: gms"),
    date_str: str = Query(..., alias="date", description="YYYYMMDD"),
) -> GmsFlatResponse:
    if lotcd != "4SS":
        raise HTTPException(status_code=400, detail="lotcd는 '4SS'만 지원합니다.")
    if unit != "weekly":
        raise HTTPException(status_code=400, detail="unit은 'weekly'만 지원합니다.")
    if process != "gms":
        raise HTTPException(status_code=400, detail="process는 'gms'만 지원합니다.")

    d = _parse_yyyymmdd(date_str)
    store = get_store("gms")
    rec = store.get_week(d)
    para = {k: _to_number(v) for k, v in rec.gmsPara.model_dump().items()}
    return GmsFlatResponse(lotcount=rec.lotcount, wfCount=rec.wfCount, **para)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
