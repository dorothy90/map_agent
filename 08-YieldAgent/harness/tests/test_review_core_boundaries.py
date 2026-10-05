import asyncio
import json
import uuid
from types import SimpleNamespace

from harness.config import Settings
from harness.nodes import Nodes
from harness.store import BudgetExceeded
from harness.testing import ScriptedModel
from harness.tools.registry import ToolRegistry, ToolSpec, ToolResult
from harness.types import Contract, ToolObservation, ResultTable


class EmptyInput(Contract):
    pass


def test_parallel_success_survives_sibling_budget_cutoff(monkeypatch):
    async def scenario():
        async def handler(*args):
            return ToolResult()
        registry = ToolRegistry()
        for name in ('first', 'second'):
            registry.add(ToolSpec(name, name, EmptyInput, handler))
        node = Nodes(ScriptedModel([]), registry,
            SimpleNamespace(settings=Settings(), remaining_seconds=lambda: 180), '')
        observation = ToolObservation(principal_id='p', session_id='s', run_id='r',
            invocation_id='a', result_id='saved', tool_name='first', columns=['value'],
            total_rows=1, preview_rows=[{'value': 7}])
        first_done = asyncio.Event()
        class Executor:
            async def execute(self, call):
                if call['name'] == 'first':
                    first_done.set()
                    return observation
                await first_done.wait()
                raise BudgetExceeded('tools_limit')
        async def executor(state):
            return Executor()
        monkeypatch.setattr(node, 'investigation_executor', executor)
        result = await node.execute({'run_id': 'r', 'messages': [], 'observations': [],
            'pending': [{'name': 'first', 'id': 'a', 'args': {}}, {'name': 'second', 'id': 'b', 'args': {}}]})
        assert result['force_finalize'] is True
        assert result['pending'] == []
        assert result['active_result_ids'] == ['saved']
        assert result['observations'][0]['result_id'] == 'saved'
        replies = [message for message in result['messages'] if message.type == 'tool']
        assert [reply.tool_call_id for reply in replies] == ['a', 'b']
        assert json.loads(replies[0].content)['result_id'] == 'saved'
    asyncio.run(scenario())


def test_child_can_run_isolated_python_and_reuses_parent_budget(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver
    from harness.delegation import register_delegation
    from harness.executor import ExecutionContext, ToolExecutor
    from harness.runtime.container import ContainerRuntime
    from harness.store import HarnessStore
    from harness.tools.registry import domain_registry

    async def scenario():
        store = HarnessStore(database='harness_review_child_' + uuid.uuid4().hex)
        payloads = []
        async def execute(self, code, datasets):
            payloads.append(datasets)
            return {'status': 'success', 'tables': [{'table_id': 'sum', 'title': 'Sum',
                'rows': [{'sum': 7}], 'complete': True}], 'plots': [], 'stdout': '', 'stderr': ''}
        monkeypatch.setattr(ContainerRuntime, 'execute', execute)
        try:
            await store.setup()
            run = await store.start_run('p', 's', 'r', 'delegate computation')
            run = await store.acquire(run['run_id'], 'worker')
            source = await store.put_result('p', 's', {'run_id': run['run_id'], 'invocation_id': 'source',
                'tool_name': 'fixture', 'provenance': {'data_origin': 'fixture'}}, [], [], tables=[
                    ResultTable(table_id='values', title='Values', columns=['n'], rows=[{'n': 7}], complete=True)])
            registry = domain_registry()
            model = lambda: ScriptedModel([
                {'tool': 'run_python', 'arguments': {'code': 'emit_table(df)', 'input_result_ids': [source.result_id]}},
                {'finish': {'answer': '합계는 7입니다.'}}])
            register_delegation(registry, model, InMemorySaver())
            ctx = ExecutionContext(store, Settings(), run, protected_tokens=1000, protected_models=2, protected_seconds=180)
            result = await ToolExecutor(registry, ctx).execute({'name': 'delegate_readonly', 'id': 'delegate',
                'args': {'question': 'Compute the sum', 'allowed_tools': ['run_python'], 'result_ids': [source.result_id]}})
            assert result.status == 'success', result.summary
            assert payloads == [{source.result_id: {'values': {'rows': [{'n': 7}], 'columns': ['n']}}}]
            current = await store.get_run('p', run['run_id'])
            assert current['usage']['children'] == 1
            assert current['usage']['tools'] == 2
            assert current['usage']['models'] == 3
            calls = await store.calls.find({'run_id': run['run_id'], 'tool_name': 'run_python'}).to_list(None)
            assert len(calls) == 1 and calls[0]['status'] == 'succeeded'
            assert calls[0]['result_id'] in result.source_result_ids
        finally:
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())


def test_memory_claim_failure_does_not_replace_verified_answer(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver
    from harness.control import RunController
    from harness.store import HarnessStore
    async def scenario():
        store = HarnessStore(database='harness_review_memory_' + uuid.uuid4().hex)
        service = RunController(store, Settings(), InMemorySaver(),
            model_factory=lambda: ScriptedModel([{'finish': {'answer': '검증된 답변입니다.'}}]))
        try:
            await store.setup()
            original = store.runs.update_one
            async def fail_memory_claim(query, update, *args, **kwargs):
                if isinstance(update, dict) and update.get('$set', {}).get('memory_update', {}).get('status') == 'running':
                    raise ConnectionError('fixture memory claim outage')
                return await original(query, update, *args, **kwargs)
            monkeypatch.setattr(store.runs, 'update_one', fail_memory_claim)
            run = await service.start('p', 's', 'r', '인사')
            await service.tasks[run['run_id']]
            result = await store.get_run('p', run['run_id'])
            assert result['status'] == 'completed', result.get('stop_reason')
            assert result['answer'] == '검증된 답변입니다.'
        finally:
            await service.close()
            await store.client.drop_database(store.db.name)
            store.client.close()
    asyncio.run(scenario())
