# FastAPI (주차 데이터) 예제

## 제공 API

- `GET /pt1h/weekly?lotcd=4SS&unit=weekly&process=pt1h&date=YYYYMMDD`
  - pt1h 파라미터 데이터 (VTH, IDSAT, IDLIN 등 25개)
  - 데이터: `fastapi_app/data/pt1h_weekly.json`

- `GET /pt1c/weekly?lotcd=4SS&unit=weekly&process=pt1c&date=YYYYMMDD`
  - pt1c 파라미터 데이터 (pt1h와 동일 구조)
  - 데이터: `fastapi_app/data/pt1c_weekly.json`

- `GET /gms/weekly?lotcd=4SS&unit=weekly&process=gms&date=YYYYMMDD`
  - GMS 파라미터 데이터 (cum0, cum2, fab, prb, pnt)
  - 데이터: `fastapi_app/data/gms_weekly.json`

- `GET /health` — 상태 확인

## 실행 방법(uv 사용)

```bash
uv sync
uv run uvicorn fastapi_app.main:app --reload --port 8000
```

## 호출 예시

```bash
curl "http://127.0.0.1:8000/pt1h/weekly?lotcd=4SS&unit=weekly&process=pt1h&date=20260209"
curl "http://127.0.0.1:8000/pt1c/weekly?lotcd=4SS&unit=weekly&process=pt1c&date=20260209"
curl "http://127.0.0.1:8000/gms/weekly?lotcd=4SS&unit=weekly&process=gms&date=20260209"
```

