from yield_viz import GMS_COLUMNS
from harness.tests.test_report_only_contract import fake_period_report
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


def test_render_yield_table_saves_html_and_returns_artifact_ref(fake_period_report):
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

        async def readchunk(self):
            data, self.data = self.data, b""
            return data

        def close(self):
            self.data = b""

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
        metadata = next(table.rows for table in result.tables if table.table_id == "column_metadata")
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
    empty = module._render_report(module._ReportInput(title="빈 리포트", rows=[]), "4SA", module._fetch_parameter_metadata("4SA"), [])
    assert {k: empty.scope[k] for k in ("title", "row_count", "column_count", "parameter_count")} == {
        "title": "빈 리포트", "row_count": 0, "column_count": 12, "parameter_count": 4,
    }
    assert empty.artifacts[0]["mime"] == "text/html"

    escaped = module._render_report(module._ReportInput(title="report", periods=[{
            "period": "2026-W45", "label": "<script>alert('x')</script>",
        }]), "4SA", module._fetch_parameter_metadata("4SA"), []).artifacts[0]["data"]
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in escaped
    assert "<script>alert('x')</script>" not in escaped


def test_report_result_counts_match_the_rendered_table_without_changing_html():
    module = importlib.import_module("harness.tools.table_artifact")
    args = module._ReportInput(
        title="4SA weekly report",
        product_label="4SA",
        rows=[
            {"period": "2026-W43", "process": "PT1H TEST", "param": "VTH", "value": 1.234},
            {"period": "2026-W44", "process": "PT1C TEST", "param": "CFTA", "value": 3.456},
        ],
    )
    expected_html = report_html(args)

    result = module._render_report(args, "4SA", module._fetch_parameter_metadata("4SA"), [])

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
    args = module._ReportInput(
        title="counts only",
        periods=[
            {"period": "2026-W43", "lot_count": 7, "wf_count": 14},
            {"period": "2026-W44", "lot_count": 8, "wf_count": 16},
        ],
    )
    expected_html = report_html(args)

    result = module._render_report(args, "4SA", module._fetch_parameter_metadata("4SA"), [])

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
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        importlib.import_module("harness.tools.table_artifact").YieldTableInput(
                title="invalid mapping",
            **{column_field: {}},
        )
