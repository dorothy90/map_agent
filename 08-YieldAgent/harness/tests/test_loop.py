import asyncio
import pytest


def test_result_drives_additional_tool_call():
    from harness.testing import run_scripted
    report = asyncio.run(run_scripted(query="수율과 관련 이력을 확인", script=[
        {"tool": "query_yield", "arguments": {"lotcd": "4SS", "ref_date": "2026-09-01"}},
        {"tool": "query_lot_history", "arguments": {"lot_ids": ["TEST001"]}},
        {"finish": {"answer": "자료가 없어 확인할 수 없습니다.", "limitations": ["자료 없음"]}},
    ], tool_results={"query_yield": {"rows": [], "status": "empty"}, "query_lot_history": {"rows": [], "status": "empty"}}))
    assert [c["name"] for c in report["tool_calls"]] == ["query_yield", "query_lot_history"]
    assert any(m.get("name") == "query_yield" for m in report["model_observations"])


def test_tool_limit_does_not_claim_completion():
    from harness.testing import run_scripted
    report = asyncio.run(run_scripted(query="계속 조사", settings={"tool_limit": 1}, script=[
        {"tool": "query_lot_history", "arguments": {"lot_ids": ["A"]}},
        {"tool": "query_lot_history", "arguments": {"lot_ids": ["B"]}},
    ], tool_results={"query_lot_history": {"rows": []}}))
    assert report["status"] == "partial"
    assert len(report["tool_calls"]) == 1


def test_interrupt_resume_does_not_replay_tools():
    from harness.testing import interrupt_roundtrip
    result = asyncio.run(interrupt_roundtrip())
    assert result["questions"] == 1
    assert result["model_calls_before_resume"] == 1
    assert result["answer"] == "안내 완료"


def test_failed_verification_is_returned_as_finish_tool_feedback():
    import json
    from harness.testing import run_scripted
    report = asyncio.run(run_scripted(query="안내", script=[
        {"finish": {"answer": "안내", "result_ids": ["missing"]}},
        {"finish": {"answer": "안내"}},
    ], tool_results={}))
    feedback = [json.loads(m["content"]) for m in report["model_observations"] if m.get("name") == "finish"]
    assert feedback[-1]["status"] == "rejected"
    assert feedback[-1]["issues"][0]["code"] == "missing_evidence"
    assert report["status"] == "completed"


def test_model_loads_only_selected_tool_schemas():
    from harness.testing import setup_case, initial_state

    async def scenario():
        store, run, model, graph = await setup_case([
            {"tool": "load_tools", "arguments": {"names": ["query_yield"]}},
            {"tool": "query_yield", "arguments": {"lotcd": "DEMO", "ref_date": "2026-09-12"}},
            {"finish": {"answer": "자료 없음"}},
        ], {"query_yield": {"rows": [], "status": "empty"}})
        seen, bind = [], model.bind_tools
        def record(tools, **kwargs):
            seen.append({tool["function"]["name"] for tool in tools})
            return bind(tools, **kwargs)
        model.bind_tools = record
        try:
            result = await graph.ainvoke(initial_state(run, "조회"), {"configurable": {"thread_id": run["run_id"]}})
            assert result["status"] == "completed"
            assert "load_tools" in seen[0] and "query_yield" in seen[0] and "query_wads" not in seen[0]
            assert "query_yield" in seen[1] and "query_wads" not in seen[1]
            assert result["loaded_tools"] == ["query_yield"]
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('mode, expected', [
    (None, ['query_wads', 'inspect_wads_coverage']),
    ('replace', ['inspect_wads_coverage']),
])
def test_loading_more_tools_preserves_existing_schemas_unless_replaced(mode, expected):
    from harness.testing import setup_case, initial_state

    async def scenario():
        arguments = {'names': ['inspect_wads_coverage', 'inspect_wads_coverage']}
        if mode:
            arguments['mode'] = mode
        store, run, model, graph = await setup_case([
            {'tool': 'load_tools', 'arguments': {'names': ['query_wads']}},
            {'tool': 'load_tools', 'arguments': arguments},
            {'finish': {'answer': '도구 준비 확인'}},
        ], {})
        seen, bind = [], model.bind_tools
        def record(tools, **kwargs):
            seen.append({tool['function']['name'] for tool in tools})
            return bind(tools, **kwargs)
        model.bind_tools = record
        try:
            result = await graph.ainvoke(initial_state(run, '도구 준비'),
                {'configurable': {'thread_id': run['run_id']}})
            assert result['status'] == 'completed'
            assert result['loaded_tools'] == expected
            assert seen[-1] & {'query_wads', 'inspect_wads_coverage'} == set(expected)
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_declared_core_tools_are_available_without_discovery():
    from types import SimpleNamespace
    from langchain_core.messages import AIMessage
    from harness.config import Settings
    from harness.nodes import Nodes
    from harness.testing import ScriptedModel
    from harness.tools.registry import domain_registry
    async def scenario():
        seen = []
        model = ScriptedModel([])
        original = model.bind_tools
        def bind(tools, **kwargs):
            seen.append({t['function']['name'] for t in tools})
            return original(tools, **kwargs)
        model.bind_tools = bind
        async def check(): return {'usage': {'tokens': 0, 'models': 0, 'tools': 0}}
        async def call(*args, **kwargs): return AIMessage(content='ready')
        node = Nodes(model, domain_registry(), SimpleNamespace(settings=Settings(), check=check, model_call=call), '')
        await node.think({'goal': {}, 'observations': [], 'messages': []})
        assert {'read_result', 'run_python', 'recall_session', 'query_yield', 'analyze_yield', 'query_defect_yield'} <= seen[-1]
        assert 'query_wads' not in seen[-1]
    asyncio.run(scenario())
