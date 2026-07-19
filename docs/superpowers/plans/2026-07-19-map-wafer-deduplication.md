# Map Wafer Deduplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure map rendering uses only the latest `LANGGRAPH_DATA` row for each `(lot_id, wf_id)` wafer.

**Architecture:** Add one pure helper in `map_agent.py` that reduces accumulated Oracle rows by wafer key and greatest `end_tm`. Call it once at the common return point so every query path and every downstream map/list/count consumer receives identical deduplicated data.

**Tech Stack:** Python 3, pytest, Oracle row dictionaries

## Global Constraints

- A wafer key is exactly `(lot_id, wf_id)`.
- Keep the row with the greatest non-null `end_tm`; a missing timestamp is older than a real timestamp.
- Do not change WADS selection, SQL filters, map rendering, or lot-ID variant rules.
- Do not add keyword, regex, phrase-list, or other semantic hardcoding.

---

### Task 1: Select the latest row per wafer

**Files:**
- Create: `08-YieldAgent/tests/unit/test_map_wafer_deduplication.py`
- Modify: `08-YieldAgent/map_agent.py:229-230`

**Interfaces:**
- Consumes: `list[dict]` Oracle result rows containing `lot_id`, `wf_id`, and nullable `end_tm`.
- Produces: `_latest_wafer_rows(rows: list[dict]) -> list[dict]`, preserving first-seen wafer-key order while replacing its value with a newer row.

- [x] **Step 1: Write the failing unit tests**

```python
from datetime import datetime

import map_agent
from map_agent import _latest_wafer_rows


def test_latest_wafer_rows_keeps_newest_end_tm_per_wafer():
    old = {"lot_id": "4SS123", "wf_id": 1, "end_tm": datetime(2026, 7, 1), "map_val_json": "old"}
    newest = {"lot_id": "4SS123", "wf_id": 1, "end_tm": datetime(2026, 7, 2), "map_val_json": "new"}
    other = {"lot_id": "4SS123", "wf_id": 2, "end_tm": datetime(2026, 7, 1), "map_val_json": "other"}

    assert _latest_wafer_rows([old, other, newest]) == [newest, other]


def test_latest_wafer_rows_prefers_timestamp_over_missing_end_tm():
    missing = {"lot_id": "4SS123", "wf_id": 1, "end_tm": None}
    dated = {"lot_id": "4SS123", "wf_id": 1, "end_tm": datetime(2026, 7, 1)}

    assert _latest_wafer_rows([missing, dated]) == [dated]
```

- [x] **Step 2: Run the focused test and verify RED**

Run: `uv run pytest 08-YieldAgent/tests/unit/test_map_wafer_deduplication.py -q`

Expected: collection fails because `_latest_wafer_rows` does not exist.

- [x] **Step 3: Implement the minimum reducer and apply it once**

```python
def _latest_wafer_rows(rows: list[dict]) -> list[dict]:
    latest_by_wafer: dict[tuple[object, object], dict] = {}
    for row in rows:
        key = (row.get("lot_id"), row.get("wf_id"))
        previous = latest_by_wafer.get(key)
        row_end_tm = row.get("end_tm")
        previous_end_tm = previous.get("end_tm") if previous else None
        if previous is None or (
            row_end_tm is not None
            and (previous_end_tm is None or row_end_tm > previous_end_tm)
        ):
            latest_by_wafer[key] = row
    return list(latest_by_wafer.values())
```

Replace the common result return with:

```python
results = _latest_wafer_rows(results)
logger.info("[MapAgent] _query_wafer_data 완료: %d wafers", len(results))
return results
```

- [x] **Step 4: Run focused and relevant unit tests**

Run:

```bash
uv run pytest 08-YieldAgent/tests/unit/test_map_wafer_deduplication.py 08-YieldAgent/tests/unit/test_primary_artifact_refs.py -q
```

Expected: all selected tests pass.

- [x] **Step 5: Exercise the downstream `show_wafer_map()` path**

Add this focused integration test to the same test file:

```python
def test_query_wafer_data_applies_latest_row_reducer(monkeypatch):
    columns = ["lot_id", "wf_id", "map_val_json", "fab_id", "lot_cd", "start_tm", "end_tm"]
    rows = [
        ("4SS123", 1, "old", "F", "4SS", None, datetime(2026, 7, 1)),
        ("4SS123", 2, "other", "F", "4SS", None, datetime(2026, 7, 1)),
        ("4SS123", 1, "new", "F", "4SS", None, datetime(2026, 7, 2)),
    ]

    class FakeCursor:
        description = [(name,) for name in columns]

        def execute(self, sql, params):
            pass

        def fetchall(self):
            return rows

        def close(self):
            pass

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

    monkeypatch.setattr(map_agent, "_get_oracle_connection_common", FakeConnection)

    result = map_agent._query_wafer_data(lot_ids="4SS123")

    assert [(row["wf_id"], row["map_val_json"]) for row in result] == [(1, "new"), (2, "other")]
```

Run:

```bash
uv run pytest 08-YieldAgent/tests/unit/test_map_wafer_deduplication.py -q
```

Expected: all three tests pass through the same `_query_wafer_data()` path used by `show_wafer_map()`.

- [x] **Step 6: Review and commit**

Run:

```bash
git diff --check
git status --short
```

Commit only the plan, unit test, and `map_agent.py` changes with a concise Conventional Commit message.
