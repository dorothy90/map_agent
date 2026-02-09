# FastAPI (주차 데이터) 예제

## 제공 API

- `GET /pt1h/weekly?lotcd=4SS&unit=weekly&process=pt1h&date=YYYYMMDD`
  - 요청한 `date`가 속한 **이번 주차(ISO week)**의 데이터를 반환
  - 반환 항목: `lotcount`, `wfCount`, `A~G`
  - 데이터는 실행 시점 기준으로 **최근 약 2개월(10주)**를 자동 생성/유지하며 `fastapi_app/data/pt1h_weekly.json`에 저장됩니다.

## 실행 방법(uv 사용)

```bash
uv sync
uv run uvicorn fastapi_app.main:app --reload --port 8000
```

## 호출 예시

```bash
curl "http://127.0.0.1:8000/pt1h/weekly?lotcd=4SS&unit=weekly&process=pt1h&date=20260209"
```

