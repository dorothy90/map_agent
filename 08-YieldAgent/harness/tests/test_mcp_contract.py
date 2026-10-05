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
