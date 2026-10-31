def test_mcp_schemas_match_registry_without_orchestration_tools():
    from harness.mcp_server import tool_definitions
    from harness.tools.registry import domain_registry
    registry = domain_registry()
    definitions = tool_definitions(registry)
    assert {tool.name for tool in definitions} == set(registry.tools)
    for tool in definitions:
        assert tool.inputSchema == registry.tools[tool.name].schema.model_json_schema()
        assert not {"principal_id", "session_id", "run_id"}.intersection(tool.inputSchema.get("properties", {}))
    assert not {"ask_user", "finish", "delegate_readonly"}.intersection(tool.name for tool in definitions)


def test_symbol_bin_option_is_exposed_with_an_empty_default():
    from harness.mcp_server import tool_definitions
    from harness.tools.defect_tools import DefectYieldInput
    from harness.tools.map_tools import MapInput
    from harness.tools.registry import domain_registry

    definitions = {tool.name: tool for tool in tool_definitions(domain_registry())}
    for name in ('render_wafer_map', 'query_defect_yield'):
        prop = definitions[name].inputSchema['properties']['symbol_bin_parameters']
        assert prop['maxItems'] == 25
    assert MapInput(lot_ids=['L'], oper='PT1H').symbol_bin_parameters == []
    assert DefectYieldInput(lotcd='P', start_date='2026-01-01', end_date='2026-02-01',
                           oper='PT1H', target_bins=['B']).symbol_bin_parameters == []
