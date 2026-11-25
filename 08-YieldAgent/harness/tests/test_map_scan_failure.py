import asyncio
import json
from contextlib import nullcontext
from types import SimpleNamespace

from harness.tools import load_wafer_maps as maps


def test_failure_on_later_page_publishes_only_completed_measurements_and_resume_offset(monkeypatch):
    def source():
        for wf, payload in [(1, '{"MAP":["1,1,?,?"]}'), (2, "invalid")]:
            yield dict(lot_id="L", wf_id=wf, oper="TEST", lot_cd="P", map_val_json=payload)

    def read(args):
        return nullcontext((source(), {}, {"lot_ids": ["L"], "wf_ids": [1, 2]}))

    async def immediate(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(maps, "_open_map_source", read)
    monkeypatch.setattr(maps, "run_blocking", immediate)
    args = maps.LoadWaferMapsInput(lot_ids=["L"], raw_page={"max_measurements": 1}, render_maps=True)
    result = asyncio.run(maps.load_wafer_maps(args, SimpleNamespace()))
    dies = result.tables[0]
    try:
        facts = result.scope["wafer_maps"]
        assert result.status == "partial" and not dies.complete
        assert facts["truncated"] and facts["next_measurement_offset"] == 1
        assert facts["measurement_count"] == dies.total_rows == 1
        assert json.loads(dies.source.readline())["wf_id"] == 1
        assert "Malformed MAP_VAL_JSON" in facts["scan_error"]
        assert all(not t.complete for t in result.tables if t.table_id != "lot_map_coverage")
        assert b"Plotly.newPlot" in result.artifacts[0]["data"].read()
    finally:
        dies.source.close()
        for artifact in result.artifacts:
            artifact["data"].close()
