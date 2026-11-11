from yield_viz import GMS_COLUMNS
from harness.tests.test_weekly_report_data import weekly_db
from harness.tests.report_table_fakes import fake_report_metadata, report_data, report_rows, report_html, COLUMNS
import asyncio
import importlib
from types import SimpleNamespace

import pytest
from bson import ObjectId
from pydantic import ValidationError


def test_render_yield_table_is_registered():
    from harness.tools.registry import domain_registry

    spec = domain_registry().tools["render_yield_table"]
    assert spec.eager is True
    assert spec.effect == "artifact"
    assert spec.read_only is True


def test_render_yield_table_saves_html_and_returns_artifact_ref(weekly_db):
    from harness.store import HarnessStore
    from harness.tools.registry import domain_registry

    class Results:
        record = None

        async def find_one(self, query):
            if self.record is None:
                return None
            if "result_id" in query and self.record["result_id"] != query["result_id"]:
                return None
            if any(self.record.get(key) != query[key] for key in ("principal_id", "session_id") if key in query):
                return None
            artifact_id = query.get("observation.artifact_refs.artifact_id")
            if artifact_id and not any(
                ref["artifact_id"] == artifact_id
                for ref in self.record["observation"]["artifact_refs"]
            ):
                return None
            return self.record

        async def insert_one(self, record):
            self.record = record

    class Download:
        def __init__(self, data):
            self.data = data

        async def read(self):
            return self.data

    class Blobs:
        def __init__(self):
            self.data = {}

        async def upload_from_stream(self, name, data):
            artifact_id = ObjectId()
            self.data[str(artifact_id)] = data
            return artifact_id

        async def open_download_stream(self, artifact_id):
            return Download(self.data[str(artifact_id)])

        async def delete(self, artifact_id):
            self.data.pop(str(artifact_id), None)

    cursor, _ = weekly_db
    cursor.yields = [r for r in cursor.yields if r[0] in ('2026-43', '2026-44')]
    cursor.counts = cursor.counts[:2]
    cursor.gms = cursor.gms[:2]

    async def scenario():
        store = HarnessStore.__new__(HarnessStore)
        store.results = Results()
        store.blobs = Blobs()
        result = await domain_registry().invoke("render_yield_table", {
            "title": "주간 수율", "lot_cd": "4SS",
            "period": {"kind": "weeks", "weeks": ["2026-43", "2026-44"]},
        }, None)

        assert "Δ = latest week − previous week" in result.summary
        assert {k: result.scope[k] for k in ("title", "row_count", "column_count", "parameter_count")} == {
            "title": "주간 수율", "row_count": 2, "column_count": 11, "parameter_count": 3,
        }
        assert len(result.artifacts) == 1
        artifact = result.artifacts[0]
        assert artifact["title"] == "주간 수율"
        assert artifact["mime"] == "text/html"
        assert "2026-44" in artifact["data"]
        assert "th-section-pt1h" in artifact["data"]
        assert '<tr class="delta">' in artifact["data"]

        observation = await store.put_result(
            "owner", "session",
            {"tool_name": "render_yield_table", "run_id": "run", "invocation_id": "call", "scope": result.scope},
            result.rows, result.artifacts, tables=result.tables,
        )
        page = await store.read_result("owner", "session", observation.result_id,
                                       limit=1000)
        assert page["table_id"] == "report_totals"
        assert page["rows"] == result.tables[0].rows
        assert await store.rows("owner", "session", observation.result_id) == result.tables[0].rows
        from harness.tools.python_tools import ReadInput, read_result
        ctx = SimpleNamespace(store=store, run={"principal_id": "owner", "session_id": "session"})
        for table in result.tables:
            read = await read_result(ReadInput(result_id=observation.result_id,
                table_id=None if table.table_id == "report_totals" else table.table_id, limit=1000), ctx)
            assert read.tables[0].table_id == table.table_id
            assert read.tables[0].rows == table.rows
        metadata = result.tables[-1].rows
        assert [r['PARAM_NM'] for r in metadata if not r['displayed']] == ['EXCLUDED']
        observation.scope = {}
        with pytest.raises(ValueError, match="Select table_id"):
            store._select_table(observation, None)
        observation.scope = {"default_table_id": "missing"}
        with pytest.raises(ValueError, match="Select table_id"):
            store._select_table(observation, None)
        assert page["complete"] and not page["truncated"]
        assert len(observation.artifact_refs) == 1
        ref = observation.artifact_refs[0]
        assert ref["mime"] == "text/html"
        assert ref["title"] == "주간 수율"
        metadata, saved = await store.artifact("owner", "session", ref["artifact_id"])
        assert metadata["mime"] == "text/html"
        assert saved.decode() == artifact["data"]

    asyncio.run(scenario())


def test_render_yield_table_handles_empty_rows_and_escapes_labels():
    module = importlib.import_module("harness.tools.table_artifact")
    empty = asyncio.run(module.render_yield_table(module.YieldTableInput(
        title="빈 리포트", rows=[],
    ), None))
    assert {k: empty.scope[k] for k in ("title", "row_count", "column_count", "parameter_count")} == {
        "title": "빈 리포트", "row_count": 0, "column_count": 12, "parameter_count": 4,
    }
    assert empty.artifacts[0]["mime"] == "text/html"

    escaped = asyncio.run(module.render_yield_table(module.YieldTableInput(
        title="report", periods=[{
            "period": "2026-W45", "label": "<script>alert('x')</script>",
        }],
    ), None)).artifacts[0]["data"]
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in escaped
    assert "<script>alert('x')</script>" not in escaped


def test_report_result_ids_render_identically_to_inline_rows():
    module = importlib.import_module("harness.tools.table_artifact")
    inline = module.YieldTableInput(
        title="4SA weekly report",
        product_label="4SA",
        rows=[
            {"period": "2026-W43", "process": "PT1H TEST", "param": "VTH", "value": 1.234},
            {"period": "2026-W43", "process": "PT1C TEST", "param": "VTH", "value": 3.456},
            {"period": "2026-W44", "process": "PT1H TEST", "param": "VTH", "value": 1.244},
        ],
        periods=[
            {"period": "2026-W43", "lot_count": 7, "wf_count": 14,
             "gms": {"cum0": 4.5, "cum2": 4.6, "fab": 4.7, "prb": 4.8, "pnt": 4.9}},
            {"period": "2026-W44", "lot_count": 8, "wf_count": 16,
             "gms": {"cum0": 5.5, "cum2": 5.6, "fab": 5.7, "prb": 5.8, "pnt": 5.9}},
        ],
    )
    saved = {
        "long": [
            {"WEEK": "2026-W43", "PROCESS": "PT1H TEST", "PARAM": "VTH", "avg_value": 1.234},
            {"WEEK": "2026-W43", "PROCESS": "PT1C TEST", "PARAM": "VTH", "avg_value": 3.456},
            {"WEEK": "2026-W44", "PROCESS": "PT1H TEST", "PARAM": "VTH", "avg_value": 1.244},
        ],
        "counts": [
            {"WEEK": "2026-W43", "LOTS": 7, "WAFERS": 14},
            {"WEEK": "2026-W44", "LOTS": 8, "WAFERS": 16},
        ],
        "gms": [
            {"WEEK": "2026-W43", "CUM0": 4.5, "CUM2": 4.6, "FAB": 4.7, "PRB": 4.8, "PNT": 4.9},
            {"WEEK": "2026-W44", "CUM0": 5.5, "CUM2": 5.6, "FAB": 5.7, "PRB": 5.8, "PNT": 5.9},
        ],
    }

    class Store:
        async def read_result(self, principal_id, session_id, result_id, **kwargs):
            assert (principal_id, session_id) == ("owner", "session")
            rows = saved[result_id]
            columns = kwargs.get("columns")
            if columns is None:
                return {"columns": list(saved[result_id][0]), "rows": saved[result_id][:1]}
            return {"rows": [{key: row.get(key) for key in columns} for row in rows]}

    by_id = module.YieldTableInput(
        title="4SA weekly report",
        product_label="4SA",
        long_result_id="long",
        long_columns={"period": "WEEK", "process": "PROCESS", "param": "PARAM", "value": "avg_value"},
        counts_result_id="counts",
        counts_columns={"period": "WEEK", "lot": "LOTS", "wf": "WAFERS"},
        gms_result_id="gms",
        gms_columns={"period": "WEEK", "cum0": "CUM0", "cum2": "CUM2", "fab": "FAB", "prb": "PRB", "pnt": "PNT"},
    )
    ctx = SimpleNamespace(
        store=Store(), run={"principal_id": "owner", "session_id": "session"},
    )

    expected = asyncio.run(module.render_yield_table(inline, None))
    actual = asyncio.run(module.render_yield_table(by_id, ctx))

    assert actual.summary == expected.summary
    assert actual.scope == expected.scope
    assert actual.artifacts == expected.artifacts
    assert actual.source_result_ids == ["long", "counts", "gms"]


def test_report_result_counts_match_the_rendered_table_without_changing_html():
    module = importlib.import_module("harness.tools.table_artifact")
    args = module.YieldTableInput(
        title="4SA weekly report",
        product_label="4SA",
        rows=[
            {"period": "2026-W43", "process": "PT1H TEST", "param": "VTH", "value": 1.234},
            {"period": "2026-W44", "process": "PT1C TEST", "param": "CFTA", "value": 3.456},
        ],
    )
    expected_html = report_html(args)

    result = asyncio.run(module.render_yield_table(args, None))

    rendered = report_data(args)
    assert {k: result.scope[k] for k in ("title", "row_count", "column_count", "parameter_count")} == {
        "title": "4SA weekly report",
        "row_count": len(rendered.rows),
        "column_count": 12,
        "parameter_count": 4,
    }
    assert rendered.column_keys == ["week", "lotcount", "wfCount", "VTH", "IDSAT", "pt1c_PT1C", "pt1c_CFTA", *(f"gms_{p}" for p in GMS_COLUMNS)]
    assert rendered.parameter_keys == ["VTH", "IDSAT", "pt1c_PT1C", "pt1c_CFTA"]
    assert "TRIM(YIELD_TABLE)='o' shown" in result.summary
    assert result.artifacts[0]["data"] == expected_html


def test_report_counts_only_keeps_metadata_parameter_columns():
    module = importlib.import_module("harness.tools.table_artifact")
    args = module.YieldTableInput(
        title="counts only",
        periods=[
            {"period": "2026-W43", "lot_count": 7, "wf_count": 14},
            {"period": "2026-W44", "lot_count": 8, "wf_count": 16},
        ],
    )
    expected_html = report_html(args)

    result = asyncio.run(module.render_yield_table(args, None))

    assert {k: result.scope[k] for k in ("title", "row_count", "column_count", "parameter_count")} == {
        "title": "counts only",
        "row_count": 2,
        "column_count": 12,
        "parameter_count": 4,
    }
    assert "TRIM(YIELD_TABLE)='o' shown" in result.summary
    assert result.artifacts[0]["data"] == expected_html


@pytest.mark.parametrize("column_field", [
    "long_columns", "counts_columns", "gms_columns",
])
def test_report_rejects_explicit_column_mapping_without_result_id(column_field):
    with pytest.raises(ValidationError, match=f"{column_field} requires"):
        importlib.import_module("harness.tools.table_artifact").YieldTableInput(
                title="invalid mapping",
            **{column_field: {}},
        )


def test_partial_gms_mapping_does_not_request_unmapped_optional_columns():
    module = importlib.import_module("harness.tools.table_artifact")
    requested = []

    class Store:
        async def read_result(self, principal_id, session_id, result_id, **kwargs):
            available = ["PERIOD_DATE", "CUM0_YIELD", "FAB_YIELD", "PRB_YIELD"]
            columns = kwargs.get("columns")
            if columns is None:
                return {"columns": available, "rows": []}
            requested.append(columns)
            if not set(columns).issubset(available):
                raise ValueError("Unknown column")
            return {"rows": [{
                "PERIOD_DATE": "2026-W43",
                "CUM0_YIELD": 86.51,
                "FAB_YIELD": 96.01,
                "PRB_YIELD": 90.23,
            }]}

    args = module.YieldTableInput(
        title="partial GMS",
        gms_result_id="gms",
        gms_columns={
            "period": "PERIOD_DATE",
            "cum0": "CUM0_YIELD",
        },
    )
    ctx = SimpleNamespace(
        store=Store(), run={"principal_id": "owner", "session_id": "session"},
    )

    result = asyncio.run(module.render_yield_table(args, ctx))

    assert result.status is None
    assert requested == [["PERIOD_DATE", "CUM0_YIELD", "FAB_YIELD", "PRB_YIELD"]]
    assert result.scope["column_count"] == 12
    assert result.scope["parameter_count"] == 4


def test_unknown_saved_column_names_mapping_key_and_available_columns():
    module = importlib.import_module("harness.tools.table_artifact")

    class Store:
        async def read_result(self, principal_id, session_id, result_id, **kwargs):
            if kwargs.get("columns") is not None:
                raise ValueError("Unknown column")
            return {
                "columns": ["WEEK", "lot_count", "wf_count"],
                "rows": [{"WEEK": "2026-W43", "lot_count": 7, "wf_count": 14}],
            }

    args = module.YieldTableInput(
        title="bad mapping",
        counts_result_id="counts",
        counts_columns={"period": "WEEK", "lot": "LOTS", "wf": "wf_count"},
    )
    ctx = SimpleNamespace(
        store=Store(), run={"principal_id": "owner", "session_id": "session"},
    )

    with pytest.raises(
        ValueError,
        match=(
            "result counts mapping key 'lot' refers to unknown column 'LOTS'; "
            "available columns: WEEK, lot_count, wf_count"
        ),
    ):
        asyncio.run(module.render_yield_table(args, ctx))


@pytest.mark.parametrize("result_id", ["unknown", "foreign-user-result"])
def test_report_result_id_unavailable_returns_factual_error(result_id):
    module = importlib.import_module("harness.tools.table_artifact")

    class Store:
        async def read_result(self, principal_id, session_id, result_id, **kwargs):
            raise PermissionError("Result unavailable")

    args = module.YieldTableInput(
        title="report", long_result_id=result_id,
    )
    ctx = SimpleNamespace(
        store=Store(), run={"principal_id": "owner", "session_id": "session"},
    )

    result = asyncio.run(module.render_yield_table(args, ctx))

    assert result.status == "error"
    assert result.summary == f"Result unavailable: {result_id}"
    assert result.artifacts == []


def test_generated_report_html_posts_observed_height():
    module = importlib.import_module("harness.tools.table_artifact")
    html = report_html(module.YieldTableInput(
        title="small", rows=[{"period": "2026-W45", "process": "pt1h", "param": "VTH", "value": 1}],
    ))

    assert "width: max-content" in html
    assert "type: 'yield-table-height'" in html
    assert "document.body.getBoundingClientRect().height" in html
    assert "new ResizeObserver(sendHeight).observe(document.body)" in html


@pytest.mark.parametrize("mapping", [
    {}, {"cum0": "CUSTOM", "period": "CUSTOM_PERIOD"},
    {"cum0": "CUM0_YIELD", "period": "PERIOD_DATE"},
])
@pytest.mark.parametrize("extra", [False, True])
def test_gms_standard_columns_fill_only_unmapped_fields(mapping, extra):
    from harness.tools.table_artifact import YieldTableInput, _resolve_report_results
    row = {"PERIOD_DATE": "202644", "CUSTOM_PERIOD": "202643", "CUSTOM": 12,
           "CUM0_YIELD": 85, "FAB_YIELD": 96, "PRB_YIELD": 90}
    if extra:
        row.update(CUM2_YIELD=84, PNT_YIELD=98)
    class Store:
        async def read_result(self, principal, session, result_id, **kwargs):
            columns = kwargs.get("columns", list(row))
            assert set(columns) <= row.keys()
            return {"columns": columns, "rows": [{k: row[k] for k in columns}]}
    ctx = SimpleNamespace(store=Store(), run={"principal_id": "owner", "session_id": "session"})
    result = asyncio.run(_resolve_report_results(YieldTableInput(
        title="GMS", gms_result_id="gms", gms_columns=mapping), ctx))
    period = result.periods[0]
    assert period.period == ("202643" if mapping.get("period") == "CUSTOM_PERIOD" else "202644")
    assert period.gms.model_dump() == {
        "cum0": 12 if mapping.get("cum0") == "CUSTOM" else 85,
        "fab": 96, "prb": 90, "cum2": 84 if extra else None, "pnt": 98 if extra else None}
