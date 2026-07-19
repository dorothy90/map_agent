"""Opt-in verification against the real Oracle and LLM services."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lot_history_agent import _render_lot_history_html
from lot_history_insight import (
    analyze_common_process_history,
    build_common_process_history,
)
from lot_history_tools import (
    _get_oracle_connection,
    _parse_lot_ids,
    _query_lots,
)


class _CountingCursor:
    def __init__(self, cursor: Any, counter: list[int]) -> None:
        self._cursor = cursor
        self._counter = counter

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        self._counter[0] += 1
        return self._cursor.execute(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _CountingConnection:
    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self.execute_count = [0]

    def cursor(self) -> _CountingCursor:
        return _CountingCursor(self._connection.cursor(), self.execute_count)

    def close(self) -> None:
        self._connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lot-ids", required=True)
    args = parser.parse_args()
    lot_ids = _parse_lot_ids(args.lot_ids)
    if len(lot_ids) < 2:
        raise SystemExit("--lot-ids requires at least two LOTs")

    conn = _CountingConnection(_get_oracle_connection())
    try:
        all_results = _query_lots(conn, lot_ids)
    finally:
        conn.close()
    if conn.execute_count[0] != 5:
        raise SystemExit(
            f"expected exactly five Oracle statements, got {conn.execute_count[0]}"
        )

    payload = build_common_process_history(all_results)
    if not payload["common_processes"]:
        raise SystemExit("selected LOTs have no exact common process")
    insight = analyze_common_process_history(payload, {})
    html = _render_lot_history_html(all_results, insight, "success")
    assert insight["process_insights"]
    assert "공통 공정 비교" in html
    assert all(lot_id in html for lot_id in lot_ids)
    print(
        json.dumps(
            {
                "lot_ids": lot_ids,
                "execute_count": conn.execute_count[0],
                "common_processes": [
                    item["process"] for item in payload["common_processes"]
                ],
                "summary": insight["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
