import asyncio
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from harness.config import Settings
from harness.types import ToolObservation


def test_completion_reserves_leave_room_for_another_investigation_step():
    from harness.nodes import Nodes
    from harness.testing import ScriptedModel
    from harness.tools.registry import ToolRegistry
    from harness.types import TableRef

    rows = [{'wafer': str(i), 'measurement': 'detail ' * 25} for i in range(100)]
    sources = [result(str(i), run_id='run', preview_rows=rows, total_rows=100,
        tables=[TableRef(table_id='data', title='Data', columns=['wafer', 'measurement'],
            total_rows=100, preview_rows=rows, complete=True, data_ref=str(i))]) for i in range(4)]
    node = Nodes(ScriptedModel([]), ToolRegistry(), SimpleNamespace(settings=Settings()), '')
    state = {'run_id': 'run', 'goal': {}, 'observations': sources,
        'messages': [HumanMessage(content='조회 결과로 순위와 맵을 완성해줘')]}
    context, reserve, _, output, review_output, _ = node.completion_plan(state, 60000)
    # Leave a 25k-token investigation call admissible after 60k already used.
    assert 60000 + 25000 + reserve <= Settings().token_limit
    assert output <= 4096 and review_output <= 2048
    assert sources[0]['tables'][0]['preview_rows'] == rows


def result(result_id="r", **updates):
    return ToolObservation(**({"principal_id": "p", "session_id": "s", "run_id": "old",
        "invocation_id": result_id, "result_id": result_id, "tool_name": "search",
        "summary": "stored documents", "total_rows": 1,
        "preview_rows": [{"doc_id": "doc", "content": "UNIQUE_DOCUMENT_BODY", "score": 1}],
        "provenance": {"source_system": "documents"}} | updates)).model_dump(mode="json")


def test_historical_focus_is_a_reference_not_automatically_loaded_body():
    from harness.context import build_context
    obs = result()
    state = {"run_id": "current", "goal": {}, "observations": [obs, obs], "focus_result_ids": ["r"]}
    context = build_context(state, "")
    metadata = json.loads(context[1].content)
    assert len(metadata["result_index"]) == 1
    assert "UNIQUE_DOCUMENT_BODY" not in str(context)
    assert metadata["result_index"][0]["result_id"] == "r"
    assert obs["preview_rows"][0]["content"] == "UNIQUE_DOCUMENT_BODY"


def test_active_evidence_occurs_once_even_with_tool_history_and_overlapping_search():
    from harness.context import build_context
    one = result("r1", run_id="current")
    two = result("r2", run_id="current", preview_rows=[{"doc_id": "doc", "content": "UNIQUE_DOCUMENT_BODY", "score": 9}])
    state = {"run_id": "current", "goal": {}, "observations": [one, two],
        "messages": [AIMessage(content="", tool_calls=[{"id": "r1", "name": "search", "args": {}}]),
            ToolMessage(content=json.dumps(one), tool_call_id="r1", name="search")]}
    context = build_context(state, "")
    assert str(context).count("UNIQUE_DOCUMENT_BODY") == 1
    metadata = json.loads(context[1].content)
    assert {v["result_id"] for v in metadata["evidence"]} == {"r1", "r2"}
    assert context[-1].type == "tool" and context[-1].tool_call_id == "r1"
    assert one["preview_rows"][0]["content"] == two["preview_rows"][0]["content"]


def test_changed_document_and_different_source_are_not_deduplicated():
    from harness.context import evidence_views
    one = result("one")
    changed = result("changed", preview_rows=[{"doc_id": "doc", "content": "NEW_VERSION"}])
    another = result("another", provenance={"source_system": "another source"})
    views = evidence_views([one, changed, another], token_budget=8000)
    assert str(views).count("UNIQUE_DOCUMENT_BODY") == 2
    assert "NEW_VERSION" in str(views)


def test_large_rows_have_explicit_omission_without_mutating_original():
    from harness.context import evidence_views
    source = result(preview_rows=[{"value": "large" * 10000}], columns=["value"])
    view = evidence_views([source], token_budget=200)[0]
    assert view["truncated"]
    assert len(json.dumps(view, ensure_ascii=False)) < 1500
    assert len(source["preview_rows"][0]["value"]) == 50000


def test_small_aggregate_table_is_not_cut_at_an_arbitrary_ten_rows():
    from harness.context import evidence_views
    rows = [{"parameter": str(n), "count": n} for n in range(21)]
    view = evidence_views([result(preview_rows=rows, total_rows=21)], token_budget=2000)[0]
    assert view["preview_rows"] == rows
    assert not view["truncated"]


def test_model_can_select_working_evidence_without_deleting_other_results():
    from harness.nodes import Nodes
    from harness.testing import ScriptedModel
    from harness.tools.registry import ToolRegistry
    from harness.context import build_context

    async def scenario():
        first = result("first", run_id="run", preview_rows=[{"content": "USE_THIS"}])
        second = result("second", run_id="run", preview_rows=[{"content": "NOT_NEEDED_NOW"}])
        async def observation(principal, session, result_id):
            assert (principal, session) == ("p", "s")
            if result_id != "first":
                raise PermissionError("Result unavailable")
            return ToolObservation.model_validate(first)
        node = Nodes(ScriptedModel([]), ToolRegistry(), SimpleNamespace(settings=Settings(),
            store=SimpleNamespace(observation=observation)), "")
        state = {"run_id": "run", "principal_id": "p", "session_id": "s", "goal": {}, "messages": [],
            "observations": [first, second], "pending": [{"id": "select", "name": "select_results", "args": {"result_ids": ["first"]}}]}
        update = await node.execute(state)
        assert update["active_result_ids"] == ["first"]
        context = build_context({**state, **update}, "")
        assert "USE_THIS" in str(context) and "NOT_NEEDED_NOW" not in str(context)
        assert len(update["observations"]) == 2
        assert second["preview_rows"][0]["content"] == "NOT_NEEDED_NOW"
    asyncio.run(scenario())


def test_final_context_has_one_evidence_set_and_preserves_latest_request():
    from harness.context import build_context
    state = {"run_id": "current", "goal": {"original_request": "first"},
        "observations": [result(run_id="current")],
        "messages": [HumanMessage(content="first"), AIMessage(content="previous answer"), HumanMessage(content="latest correction")]}
    context = build_context(state, "", final=True)
    assert str(context).count("UNIQUE_DOCUMENT_BODY") == 1
    assert "latest correction" in str(context)


def test_saved_compaction_is_restored_without_replaying_covered_turns():
    from harness.context import session_context, restore_memory
    newest = {"run_id": "new", "query": "latest query", "answer_text": "latest answer", "result_ids": ["r"],
        "observations": [result()], "context_memory": {
            "summary": "PERSISTED_SUMMARY", "messages": [HumanMessage(content="retained question").model_dump()],
            "covered_run_ids": ["old", "new"]}}
    older = {"run_id": "old", "query": "DO_NOT_REHYDRATE", "answer": "old text", "observations": []}
    history, observations, focus = session_context([newest, older])
    assert "DO_NOT_REHYDRATE" not in str(history)
    assert "latest answer" in str(history)
    assert restore_memory([newest, older])["summary"] == "PERSISTED_SUMMARY"
    assert focus == ["r"] and observations[0]["result_id"] == "r"


def test_failed_compaction_does_not_discard_history_or_current_question():
    from harness.nodes import Nodes
    from harness.testing import ScriptedModel
    from harness.tools.registry import ToolRegistry

    async def scenario():
        calls = []
        async def check():
            return {"usage": {"tokens": 0, "models": 0, "tools": 0}}
        async def event(*args, **kwargs):
            pass
        async def model_call(model, messages, **kwargs):
            calls.append(kwargs)
            if kwargs.get("purpose") == "compaction":
                raise ValueError("summary failed")
            return AIMessage(content="available answer")
        ctx = SimpleNamespace(settings=Settings(context_tokens=4096), check=check,
            model_call=model_call, store=SimpleNamespace(event=event))
        nodes = Nodes(ScriptedModel([]), ToolRegistry(), ctx, "")
        history = [*(HumanMessage(content="old dialogue " * 600) for _ in range(8)), HumanMessage(content="KEEP CURRENT")]
        state = {"run_id": "run", "goal": {}, "messages": history, "observations": []}
        updates = await nodes.think(state)
        assert any(call.get("purpose") == "compaction" for call in calls)
        assert not updates.get("summary")
        assert updates.get("messages", [])[:len(history)] == history
        assert "KEEP CURRENT" in str(updates)
    asyncio.run(scenario())


def test_completion_reserves_both_inputs_and_actual_output_caps():
    from harness.nodes import Nodes
    from harness.testing import ScriptedModel
    from harness.tools.registry import ToolRegistry
    ctx = SimpleNamespace(settings=Settings())
    nodes = Nodes(ScriptedModel([]), ToolRegistry(), ctx, "instructions")
    state = {"run_id": "current", "goal": {"original_request": "explain"},
        "messages": [HumanMessage(content="explain")], "observations": [result(run_id="current")]}
    final_context, total, review_reserve, output, review_output, evidence_tokens = nodes.completion_plan(state, 120000)
    assert total == nodes.input_tokens(nodes.finalizer, final_context) + output + review_reserve
    review = nodes.review_context(state, {"answer": "explained"}, state["observations"], evidence_tokens)
    assert review_reserve >= nodes.input_tokens(nodes.verifier, review) + review_output
    assert output <= Settings().max_output_tokens and review_output <= Settings().max_output_tokens
    larger = {**state, "observations": [result(run_id="current", preview_rows=[{"n": n, "value": "detail" * 100} for n in range(8)], total_rows=8)]}
    assert nodes.completion_plan(larger, 120000)[1] > total


def test_new_tool_schema_pressure_triggers_compaction_of_small_history():
    from harness.nodes import Nodes
    from harness.testing import ScriptedModel
    from harness.tools.registry import ToolRegistry
    from harness.context import build_context

    async def scenario():
        archives, calls = [], []
        async def insert_one(value):
            archives.append(value)
        async def event(*args, **kwargs):
            pass
        async def check():
            return {"usage": {"tokens": 0, "models": 0, "tools": 0}}
        async def model_call(model, messages, **kwargs):
            calls.append(kwargs)
            return AIMessage(content="Goal and source r retained.")
        ctx = SimpleNamespace(settings=Settings(context_tokens=4096), check=check, model_call=model_call,
            store=SimpleNamespace(event=event, db=SimpleNamespace(harness_context_archives=SimpleNamespace(insert_one=insert_one))))
        node = Nodes(ScriptedModel([]), ToolRegistry(), ctx, "")
        state = {"run_id": "run", "principal_id": "p", "session_id": "s", "goal": {},
            "messages": [HumanMessage(content="older instruction " * 100), AIMessage(content="old answer " * 100), HumanMessage(content="CURRENT")], "observations": []}
        bound = SimpleNamespace(kwargs={"tools": [{"description": "schema " * 3000}]})
        update = await node.compact(state, bound, build_context(state, ""), await check(), 10000)
        assert calls[0]["purpose"] == "compaction"
        assert update["summary"] and "CURRENT" in str(update["messages"])
        assert archives[0]["principal_id"] == "p" and archives[0]["session_id"] == "s"
        assert update["context_archives"] == [archives[0]["_id"]]
    asyncio.run(scenario())


def test_compaction_preserves_current_question_and_complete_tool_pairs():
    from harness.context import split_for_compaction, message_groups
    messages = [HumanMessage(content="CURRENT GOAL")]
    for number in range(20):
        messages += [AIMessage(content="", tool_calls=[{"id": str(number), "name": "read_result", "args": {}}]),
            ToolMessage(content="large result " * 100, tool_call_id=str(number))]
    older, keep = split_for_compaction(messages, 1500)
    assert older and keep[0].content == "CURRENT GOAL"
    assert all(group[0].type == "ai" for group in message_groups(keep) if any(m.type == "tool" for m in group))


def test_archived_history_can_only_be_read_by_its_owner_and_session():
    import uuid
    import pytest
    from harness.store import HarnessStore
    from harness.executor import ExecutionContext
    from harness.tools.python_tools import recall_session, RecallInput

    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        try:
            await store.db.harness_context_archives.insert_one({"_id": "archive", "principal_id": "p", "session_id": "s",
                "messages": [{"type": "human", "content": "original instruction"}, {"type": "ai", "content": "original answer"}]})
            ctx = ExecutionContext(store, Settings(), {"principal_id": "p", "session_id": "s"})
            page = await recall_session(RecallInput(archive_id="archive", limit=1), ctx)
            assert page.rows == [{"type": "human", "content": "original instruction"}] and page.status == "partial"
            for principal, session in [("other", "s"), ("p", "other")]:
                ctx.run = {"principal_id": principal, "session_id": session}
                with pytest.raises(PermissionError):
                    await recall_session(RecallInput(archive_id="archive"), ctx)
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_memory_never_restores_unanswered_native_calls_after_a_stop():
    from harness.context import memory_snapshot, session_context
    state = {"messages": [HumanMessage(content="request"), AIMessage(content="checking", tool_calls=[
        {"id": "finished", "name": "read_result", "args": {"result_id": "a"}},
        {"id": "not-run", "name": "read_result", "args": {"result_id": "b"}}]),
        ToolMessage(content="available", tool_call_id="finished")]}
    memory = memory_snapshot(state)
    history, _, _ = session_context([{"run_id": "r", "query": "request", "answer_text": "stopped", "context_memory": memory}])
    calls = {c["id"] for m in history for c in getattr(m, "tool_calls", [])}
    replies = {m.tool_call_id for m in history if m.type == "tool"}
    assert calls == replies
    assert "not-run" in str(memory)


def test_failed_summary_usage_is_refreshed_before_choosing_next_stage():
    from harness.nodes import Nodes
    from harness.testing import ScriptedModel
    from harness.tools.registry import ToolRegistry

    async def scenario():
        used, stages = 7, []
        async def check():
            return {"usage": {"tokens": 1000, "models": used, "tools": 0}}
        async def event(*args, **kwargs):
            pass
        async def model_call(model, messages, **kwargs):
            nonlocal used
            stages.append(kwargs["purpose"])
            used += 1
            if kwargs["purpose"] == "compaction":
                return AIMessage(content="")
            return AIMessage(content="", tool_calls=[{"id": "f", "name": "finish", "args": {"answer": "finished"}}])
        node = Nodes(ScriptedModel([]), ToolRegistry(), SimpleNamespace(settings=Settings(model_limit=10, context_tokens=4096),
            check=check, model_call=model_call, store=SimpleNamespace(event=event)), "")
        state = {"run_id": "r", "goal": {}, "messages": [HumanMessage(content="older " * 1500), HumanMessage(content="current")]}
        await node.think(state)
        assert stages == ["compaction", "answer"]
    asyncio.run(scenario())


def test_review_reserve_accounts_for_historical_lineage_descriptors():
    from harness.nodes import Nodes
    from harness.testing import ScriptedModel
    from harness.tools.registry import ToolRegistry
    nodes = Nodes(ScriptedModel([]), ToolRegistry(), SimpleNamespace(settings=Settings()), "")
    parent = result("parent", scope={"provenance_description": "x" * 30000})
    current = result("current", run_id="run", source_result_ids=["parent"])
    state = {"run_id": "run", "goal": {}, "messages": [HumanMessage(content="current")], "observations": [parent, current]}
    _, _, reserve, _, cap, evidence_tokens = nodes.completion_plan(state, 120000)
    actual = nodes.review_context(state, {"answer": "finished", "result_ids": ["current"]}, [current, parent], evidence_tokens)
    assert reserve >= nodes.input_tokens(nodes.verifier, actual) + cap


def test_terminal_checkpoint_recovery_persists_working_memory():
    import uuid
    from langgraph.checkpoint.memory import InMemorySaver
    from harness.store import HarnessStore
    from harness.control import RunController
    from harness.executor import ExecutionContext
    from harness.graph import build_harness
    from harness.checkpoints import epoch_config
    from harness.testing import ScriptedModel, initial_state
    from harness.tools.registry import ToolRegistry

    async def scenario():
        store = HarnessStore(database="harness_test_" + uuid.uuid4().hex)
        saver = InMemorySaver()
        controller = RunController(store, Settings(), saver, model_factory=lambda: ScriptedModel([]), registry_factory=ToolRegistry)
        try:
            await store.setup()
            run = await store.start_run("p", "s", "r", "request")
            run = await store.acquire(run["run_id"], "old-worker")
            graph = build_harness(model=ScriptedModel([{"finish": {"answer": "delivered"}}]), registry=ToolRegistry(),
                store=store, checkpointer=saver, control=ExecutionContext(store, Settings(), run), instructions="")
            state = {**initial_state(run, "request"), "summary": "durable compacted facts", "context_archives": ["archive"]}
            final = await graph.ainvoke(state, await epoch_config(saver, run["run_id"], run["epoch"]))
            assert final["status"] == "completed"
            # Simulate death after graph checkpoint but before controller save.
            await store.runs.update_one({"_id": run["run_id"]}, {"$set": {"lease_until": 0}})
            await controller.recover()
            await controller.tasks[run["run_id"]]
            restored = await store.get_run("p", run["run_id"])
            assert restored["status"] == "completed"
            assert restored["context_memory"]["summary"] == "durable compacted facts"
            assert restored["context_memory"]["context_archives"] == ["archive"]
            assert restored["answer_text"] == "delivered"
        finally:
            await controller.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_actual_review_input_fits_reservation_despite_different_evidence_order():
    from harness.nodes import Nodes
    from harness.testing import ScriptedModel
    from harness.tools.registry import ToolRegistry
    node = Nodes(ScriptedModel([]), ToolRegistry(), SimpleNamespace(settings=Settings()), "")
    korean = [result(f"ko{i}", preview_rows=[{"text": "가" * 3000}] * 2, total_rows=2) for i in range(3)]
    ascii_rows = [result(f"en{i}", preview_rows=[{"text": "a" * 3000}] * 2, total_rows=2) for i in range(3)]
    state = {"run_id": "run", "goal": {}, "messages": [], "observations": [*korean, *ascii_rows]}
    _, _, reserve, _, output, evidence_tokens = node.completion_plan(state, 120000)
    # Balanced source shares remove the previous order-only overflow. A sizeable
    # candidate still exercises the actual review-input boundary with UTF-8 data.
    candidate = {"answer": "확인한 자료와 한계를 설명합니다. " * 300, "result_ids": [r["result_id"] for r in korean]}
    unbounded = node.review_context(state, candidate, korean, evidence_tokens)
    assert node.input_tokens(node.verifier, unbounded) + output > reserve
    bounded = node.bounded_review_context({**state, "evidence_tokens": evidence_tokens}, candidate, korean, reserve - output)
    assert node.input_tokens(node.verifier, bounded) + output <= reserve
    assert any(o["truncated"] for o in json.loads(bounded[-1].content)["observations"])
    assert korean[0]["preview_rows"][0]["text"] == "가" * 3000


def test_projection_shares_budget_between_sources_then_tables_without_hiding_yield():
    from harness.context import evidence_views, serialized_size
    from harness.types import TableRef
    def observation(rid, tables):
        refs = [TableRef(table_id=name, title=name, columns=list(rows[0]) if rows else [], total_rows=len(rows), preview_rows=rows, complete=True, data_ref='blob') for name, rows in tables]
        return result(rid, run_id='run', preview_rows=refs[0].preview_rows if len(refs) == 1 else [], total_rows=sum(t.total_rows for t in refs), tables=refs)
    yield_rows = [{'week': str(i), **{f'metric{j}': i + j / 100 for j in range(35)}} for i in range(4)]
    sources = [observation('wads-derived', [('daily', [{'day': str(i), 'count': i} for i in range(24)])]),
        observation('wads', [('reports', [{'report': str(i), 'text': 'detail ' * 30} for i in range(50)]),
            ('wafers', [{'wafer': i, 'description': 'wafer ' * 30} for i in range(50)])]),
        observation('yield', [('weekly', yield_rows)])]
    views = evidence_views(sources, token_budget=8000)
    assert views[-1]['preview_rows'] == yield_rows
    assert all(table.get('preview_rows') for table in views[1]['tables'])
    rows = [row for view in views for row in view['preview_rows']] + [row for view in views for table in view['tables'] for row in table.get('preview_rows', [])]
    assert sum(serialized_size(row) for row in rows) <= 8000
    for view in views:
        for table in view['tables']:
            shown = len(table.get('preview_rows', view['preview_rows']))
            assert table['projected_rows'] == shown
            assert table['omitted_rows'] == table['total_rows'] - shown
            assert table['truncated'] == (shown < table['total_rows'])
        assert view['truncated'] == any(table['truncated'] for table in view['tables'])
    assert len(sources[1]['tables'][0]['preview_rows']) == 50


def test_zero_row_single_table_projection_marks_both_omissions():
    from harness.context import evidence_views
    from harness.types import TableRef
    rows = [{'text': 'x' * 10000}]
    source = result('r', preview_rows=rows, tables=[TableRef(table_id='t', title='T', columns=['text'], total_rows=1, preview_rows=rows, complete=True, data_ref='blob')])
    view = evidence_views([source], token_budget=50)[0]
    assert view['preview_rows'] == [] and view['truncated']
    assert view['tables'][0]['truncated']
    assert view['tables'][0]['projected_rows'] == 0
    assert view['tables'][0]['omitted_rows'] == 1
    assert view['read_more']


def test_final_context_keeps_latest_public_action_context_and_marks_old_summary():
    from harness.context import build_context
    obs = result('yield', run_id='run')
    commentary = 'W34 to W35 fell; W37 has no measurements.'
    state = {'run_id': 'run', 'goal': {}, 'observations': [obs], 'summary': 'Earlier: data not queried yet.',
        'messages': [HumanMessage(content='Show yield'), AIMessage(content=commentary, tool_calls=[{'id': 'c', 'name': 'search', 'args': {}}]),
            ToolMessage(content=json.dumps(obs), tool_call_id='c', name='search')]}
    context = build_context(state, '', final=True)
    metadata = json.loads(context[1].content)
    assert metadata['recent_action_context']['commentary'] == commentary
    assert metadata['recent_action_context']['evidence_status'] == 'context_only_not_verified_evidence'
    assert metadata['recent_action_context']['results'][0]['result_id'] == 'yield'
    assert metadata['summary_scope'] == 'older_compacted_messages; current results and latest user instructions take precedence'
    assert str(context).count('UNIQUE_DOCUMENT_BODY') == 1
    assert not any(getattr(message, 'tool_calls', []) or message.type == 'tool' for message in context)


def test_review_receives_loaded_skill_contracts():
    from harness.nodes import Nodes
    from harness.testing import ScriptedModel
    from harness.tools.registry import ToolRegistry
    node = Nodes(ScriptedModel([]), ToolRegistry(), SimpleNamespace(settings=Settings()), '')
    skill = {'name': 'analysis', 'content': 'Preserve the requested population and metric.'}
    state = {'goal': {}, 'loaded_skills': {'analysis': skill}}
    messages = node.review_context(state, {'answer': 'done'}, [], 2000)
    assert any(m.type == 'system' and json.loads(m.content) == skill
        for m in messages if m.additional_kwargs.get('input_section') == 'skills')


def test_explicit_read_result_is_visible_before_older_preview_rows():
    from harness.context import evidence_views
    old = [result(str(i), preview_rows=[{'value': 'a' * 300}] * 10, total_rows=10) for i in range(4)]
    read = result('read', tool_name='read_result', preview_rows=[{'stdout': 'requested ' * 130}], total_rows=1)
    views = evidence_views([read, *old], token_budget=1000)
    assert views[0]['preview_rows'] == read['preview_rows']
    assert sum(len(json.dumps(v['preview_rows'], ensure_ascii=False)) // 2 for v in views) <= 1100


def test_older_explicit_read_does_not_hide_a_new_calculation():
    from harness.context import evidence_views
    read = result('read', tool_name='read_result', preview_rows=[{'v': 'a' * 390}] * 5, total_rows=5)
    calculated = result('calculated', tool_name='run_python', preview_rows=[{'result': 'NEW_RESULT'}], total_rows=1)
    views = evidence_views([calculated, read], token_budget=1000)
    assert views[0]['preview_rows'] == calculated['preview_rows']


def test_repeated_equivalent_retrievals_are_reported_despite_new_result_ids():
    from harness.context import build_context
    observations = [result(str(i), run_id='run', tool_name='read_result',
        preview_rows=[{'value': 10}], total_rows=1) for i in range(3)]
    state = {'goal': {}, 'run_id': 'run', 'observations': observations}
    metadata = json.loads(next(m.content for m in build_context(state, '')
        if m.additional_kwargs.get('input_section') == 'context'))
    assert metadata['repeated_observation_notice']['count'] == 3
    observations[-1]['preview_rows'] = [{'value': 11}]
    metadata = json.loads(next(m.content for m in build_context(state, '')
        if m.additional_kwargs.get('input_section') == 'context'))
    assert metadata['repeated_observation_notice'] is None
