def test_domain_registry_exposes_all_existing_capabilities():
    from harness.tools.registry import domain_registry
    tools = domain_registry().tools
    assert {"query_yield", "query_wads", "get_wads_report", "query_lot_history", "render_wafer_map", "search_fail_history", "query_relation", "query_good_bad_groups", "analyze_mining", "export_report", "read_result", "run_python"}.issubset(tools)


def test_mining_requires_live_api_instead_of_dummy(monkeypatch):
    import asyncio
    import pytest
    from harness.tools.registry import domain_registry
    monkeypatch.delenv("HARNESS_MINING_API_URL", raising=False)
    with pytest.raises(ValueError, match="HARNESS_MINING_API_URL"):
        asyncio.run(domain_registry().invoke("analyze_mining", {"lotcd": "4SS", "fail_type": "X", "category": "PT1H", "group_good": ["A"], "group_bad": ["B"]}, None))


def test_map_connection_error_is_not_empty_data(monkeypatch):
    import pytest
    import map_agent
    def broken():
        raise ConnectionError("offline")
    monkeypatch.setattr(map_agent, "_get_oracle_connection_common", broken)
    with pytest.raises(ConnectionError):
        map_agent._query_wafer_data(lot_id="TEST001", strict=True)
