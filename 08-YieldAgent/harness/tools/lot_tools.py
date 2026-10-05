from ..runtime.domain_process import run_blocking
from pydantic import Field

from ..types import Contract, ResultTable
from .registry import ToolResult, ToolSpec


class LotInput(Contract):
    lot_ids: list[str] = Field(min_length=1, max_length=50)


def _query(lot_ids):
    from lot_history_tools import _query_lot, _get_oracle_connection
    with _get_oracle_connection() as connection:
        return {lot_id: _query_lot(connection, lot_id) for lot_id in lot_ids}


async def query_lot_history(args, context):
    from lot_history_tools import _COLUMN_NAMES
    from lot_history_agent import _risk_level, _render_lot_history_html
    groups = await run_blocking(_query, args.lot_ids)
    tables = [ResultTable(table_id=source, title=source, columns=columns,
        rows=[row for data in groups.values() for row in data[source]], complete=True)
        for source, columns in _COLUMN_NAMES.items()]
    risks = {lot: _risk_level(data) for lot, data in groups.items()}
    html = _render_lot_history_html(groups)
    return ToolResult(tables=tables, artifacts=[{"title": "LOT 이력", "mime": "text/html", "data": html}],
        scope={**args.model_dump(), "risk_by_lot": risks},
        summary=f"LOT {len(groups)}개 · 5개 이력 표 · {sum(len(t.rows) for t in tables)}행. 위험 분류는 기존 LOT 규칙 기준.")


def register(registry):
    registry.add(ToolSpec("query_lot_history", "명시한 LOT의 FDC/Q-TIME/Trouble/Future Action/Sample Split 이력 원본 조회.", LotInput, query_lot_history))
