import importlib.util

import pytest

from harness.tools.registry import domain_registry


def test_domain_registry_exposes_only_native_capabilities():
    assert set(domain_registry().tools) == {
        "read_result", "query_db", "list_tables",
        "load_wafer_maps", "render_yield_table",
    }


@pytest.mark.parametrize("module", [
    "yield_tools", "wads_tools", "lot_tools", "analysis_tools",
    "artifact_tools", "map_tools", "document_tools", "defect_tools",
    "wafer_set_register",
])
def test_retired_tool_bundles_are_absent(module):
    assert importlib.util.find_spec("harness.tools." + module) is None
