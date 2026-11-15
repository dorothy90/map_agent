import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from harness.tools.registry import ToolRegistry, ToolResult, ToolSpec


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')
    lot_cd: str


def setup_registry(handler):
    registry = ToolRegistry()
    registry.add(ToolSpec('render_yield_table', 'report', Input, handler))
    return registry


def call(registry, args, run='run-1'):
    context = None if run is None else SimpleNamespace(run={'run_id': run, 'principal_id': 'p', 'session_id': 's'})
    return asyncio.run(registry.invoke('render_yield_table', args, context))


def test_schema_failures_are_structured_and_third_identical_call_is_blocked():
    calls = []
    async def handler(args, context):
        calls.append(args)
        return ToolResult()
    registry = setup_registry(handler)
    first = call(registry, {'rows': [{'value': 'secret-row'}]})
    assert first.status == 'error'
    assert first.scope['code'] == 'invalid_report_schema'
    assert first.scope['retryable'] is True
    assert first.scope['count'] == 1
    assert first.scope['legal_shapes']
    assert 'secret-row' not in str(first)
    call(registry, {'rows': [{'value': 'secret-row'}]})
    third = call(registry, {'rows': [{'value': 'secret-row'}]})
    assert third.scope['code'] == 'repeated_invalid_report'
    assert third.scope['retryable'] is False
    assert third.scope['count'] == 3
    assert calls == []
    assert call(registry, {'lot_cd': '4SS'}).status != 'error'


def test_fifth_failure_exhausts_run_and_blocks_later_valid_calls():
    from harness.tools.report_policy import ReportContractError
    calls = []
    async def handler(args, context):
        calls.append(args.lot_cd)
        raise ReportContractError('invalid_membership', {'reason': 'unknown lot'})
    registry = setup_registry(handler)
    for index in range(5):
        result = call(registry, {'lot_cd': str(index)})
    assert result.scope['code'] == 'report_budget_exhausted'
    assert result.scope['count'] == 5
    assert not result.scope['retryable']
    later = call(registry, {'lot_cd': 'valid'})
    assert later.scope['code'] == 'report_budget_exhausted'
    assert calls == ['0', '1', '2', '3', '4']
    assert call(registry, {'lot_cd': 'other'}, run='run-2').scope['count'] == 1


def test_success_preserved_and_membership_reference_hint_without_rows():
    from harness.tools.report_policy import ReportContractError
    success = ToolResult(scope={'report_scope_id': 'yield-report:scope1'}, source_result_ids=['source1'])
    async def handler(args, context):
        if args.lot_cd == 'good':
            return success
        raise ReportContractError('invalid_membership', 'Use group_id and lot_id columns')
    registry = setup_registry(handler)
    assert call(registry, {'lot_cd': 'good'}) is success
    failure = call(registry, {'lot_cd': 'bad'})
    assert failure.scope['successful_reports'] == [{'report_scope_id': 'yield-report:scope1', 'source_result_ids': ['source1']}]
    assert 'group_id' in str(failure.scope['membership_hint'])
    assert 'lot_id' in str(failure.scope['membership_hint'])
    assert 'exactly one table overall' in failure.scope['membership_hint']['table_selection']


def test_transient_errors_propagate_and_other_tools_are_inert_and_none_context_works():
    async def handler(args, context):
        if args.lot_cd == 'fail':
            raise RuntimeError('transient')
        return ToolResult(summary='ok')
    registry = setup_registry(handler)
    with pytest.raises(RuntimeError, match='transient'):
        call(registry, {'lot_cd': 'fail'})
    assert call(registry, {'lot_cd': 'good'}, run=None).summary == 'ok'
    registry.add(ToolSpec('other', 'other', Input, handler))
    with pytest.raises(ValueError):
        asyncio.run(registry.invoke('other', {}, None))


def test_contract_error_result_is_budgeted():
    async def handler(args, context):
        return ToolResult(status='error', scope={'code': 'invalid_membership', 'details': 'bad identifiers'})
    registry = setup_registry(handler)
    assert call(registry, {'lot_cd': 'x'}).scope['count'] == 1
    call(registry, {'lot_cd': 'x'})
    assert call(registry, {'lot_cd': 'x'}).scope['code'] == 'repeated_invalid_report'


def test_third_identical_contract_failure_skips_handler_without_losing_success():
    from harness.tools.report_policy import ReportContractError
    calls = []
    async def handler(args, context):
        calls.append(args.lot_cd)
        if args.lot_cd == 'bad':
            raise ReportContractError('invalid_membership', {'table_id': 'members'})
        return ToolResult(summary='good')
    registry = setup_registry(handler)
    call(registry, {'lot_cd': 'bad'})
    assert call(registry, {'lot_cd': 'good'}).summary == 'good'
    call(registry, {'lot_cd': 'bad'})
    assert call(registry, {'lot_cd': 'bad'}).scope['code'] == 'repeated_invalid_report'
    assert calls == ['bad', 'good', 'bad']


def test_report_budget_isolated_by_principal_session_but_not_epoch():
    async def handler(args, context):
        return ToolResult()
    registry = setup_registry(handler)
    async def invoke(principal='p', session='s', epoch=1):
        context = SimpleNamespace(run={'principal_id': principal, 'session_id': session, 'run_id': 'same-run', 'epoch': epoch})
        return await registry.invoke('render_yield_table', {}, context)
    assert asyncio.run(invoke()).scope['count'] == 1
    assert asyncio.run(invoke()).scope['count'] == 2
    assert asyncio.run(invoke(principal='other')).scope['count'] == 1
    assert asyncio.run(invoke(session='other')).scope['count'] == 1
    assert asyncio.run(invoke(epoch=2)).scope['count'] == 3


def test_handler_validation_errors_are_not_misclassified_as_input_schema_errors():
    from pydantic import ValidationError
    async def handler(args, context):
        Input.model_validate({})
    registry = setup_registry(handler)
    with pytest.raises(ValidationError):
        call(registry, {'lot_cd': 'good'})



def test_errors_include_repair_text_without_inputs_and_are_bounded():
    from harness.tools.report_policy import ReportContractError
    async def handler(args, context):
        raise ReportContractError('invalid_membership', {'message': 'Use identifier membership ' + 'x' * 3000})
    registry = setup_registry(handler)
    schema_error = call(registry, {'rows': ['secret']})
    assert 'Field required' in schema_error.summary
    assert 'secret' not in schema_error.summary
    assert schema_error.scope['details']['errors'][0]['msg']
    contract_error = call(registry, {'lot_cd': 'bad'})
    assert 'Use identifier membership' in contract_error.summary
    assert len(contract_error.summary) <= 1200


def test_missing_context_or_run_id_never_shares_budget():
    async def handler(args, context):
        return ToolResult()
    registry = setup_registry(handler)
    for index in range(8):
        assert call(registry, {}, run=None).scope['count'] == 1
        result = asyncio.run(registry.invoke('render_yield_table', {}, SimpleNamespace(run={'principal_id': 'p'})))
        assert result.scope['count'] == 1
    assert not registry._report_policy_states


def test_concurrent_render_failures_serialize_before_budget_checks():
    from harness.tools.report_policy import ReportContractError
    calls = []
    async def handler(args, context):
        calls.append(args.lot_cd)
        await asyncio.sleep(0)
        raise ReportContractError('invalid_membership', 'bad membership')
    registry = setup_registry(handler)
    async def exercise():
        context = SimpleNamespace(run={'run_id': 'run', 'principal_id': 'p', 'session_id': 's'})
        return await asyncio.gather(*(registry.invoke('render_yield_table', {'lot_cd': str(i)}, context) for i in range(10)))
    results = asyncio.run(exercise())
    assert len(calls) == 5
    assert [r.scope['count'] for r in results] == [1, 2, 3, 4, 5, 5, 5, 5, 5, 5]


def test_success_references_include_actual_report_and_membership_evidence():
    import uuid
    from harness.tools.report_policy import ReportContractError
    membership = {'kind': 'table', 'result_id': 'members', 'table_id': 'cohorts'}
    success = ToolResult(rows=[{'secret': 'raw rows'}], scope={'report_scope_id': 'yield-report:scope', 'membership': membership}, source_result_ids=['source'])
    async def handler(args, context):
        if args.lot_cd == 'good':
            return success
        raise ReportContractError('invalid_membership', 'bad membership')
    registry = setup_registry(handler)
    async def exercise():
        context = SimpleNamespace(run={'run_id': 'run', 'principal_id': 'p', 'session_id': 's'}, invocation_id='report-call')
        assert await registry.invoke('render_yield_table', {'lot_cd': 'good'}, context) is success
        return await registry.invoke('render_yield_table', {'lot_cd': 'bad'}, context)
    failure = asyncio.run(exercise())
    reference = failure.scope['successful_reports'][0]
    assert reference['result_id'] == str(uuid.uuid5(uuid.NAMESPACE_URL, 'run:report-call'))
    assert reference['membership'] == membership
    assert reference['source_result_ids'] == ['source']
    assert 'raw rows' not in str(failure)
    assert reference['result_id'] in failure.source_result_ids


class PersistentRuns:
    def __init__(self):
        self.record = {'_id': 'run', 'principal_id': 'p', 'session_id': 's', 'epoch': 1}
        self.reads = []
        self.writes = []

    def matches(self, query):
        return all(self.record.get(key) == value for key, value in query.items())

    async def find_one(self, query):
        import copy
        self.reads.append(query)
        return copy.deepcopy(self.record) if self.matches(query) else None

    async def update_one(self, query, update):
        import copy
        self.writes.append((query, update))
        matched = self.matches(query)
        if matched:
            self.record.update(copy.deepcopy(update['$set']))
        return SimpleNamespace(matched_count=int(matched))


def test_policy_restores_fresh_registry_and_new_epoch_without_resetting_budget():
    from harness.tools.report_policy import ReportContractError
    calls = []
    async def handler(args, context):
        calls.append(args.lot_cd)
        if args.lot_cd == 'good':
            return ToolResult(scope={'report_scope_id': 'yield-report:scope'}, source_result_ids=['source'])
        raise ReportContractError('invalid_membership', 'fix membership')
    runs = PersistentRuns()
    context = SimpleNamespace(run={'run_id': 'run', 'principal_id': 'p', 'session_id': 's', 'epoch': 1},
                              store=SimpleNamespace(runs=runs), invocation_id='report')
    async def exercise():
        registry = setup_registry(handler)
        await registry.invoke('render_yield_table', {'lot_cd': 'good'}, context)
        await registry.invoke('render_yield_table', {'lot_cd': 'bad'}, context)
        await registry.invoke('render_yield_table', {'lot_cd': 'bad'}, context)
        assert len(runs.reads) == 1
        runs.record['epoch'] = 2
        context.run['epoch'] = 2
        recovered = setup_registry(handler)
        third = await recovered.invoke('render_yield_table', {'lot_cd': 'bad'}, context)
        assert third.scope['code'] == 'repeated_invalid_report'
        assert third.scope['count'] == 3
        assert third.scope['successful_reports'][0]['report_scope_id'] == 'yield-report:scope'
        await recovered.invoke('render_yield_table', {'lot_cd': 'different'}, context)
        await recovered.invoke('render_yield_table', {'lot_cd': 'another'}, context)
        fresh = setup_registry(handler)
        blocked = await fresh.invoke('render_yield_table', {'lot_cd': 'good'}, context)
        assert blocked.scope['code'] == 'report_budget_exhausted'
        assert blocked.scope['count'] == 5
    asyncio.run(exercise())
    assert calls == ['good', 'bad', 'bad', 'different', 'another']
    import json
    assert json.loads(json.dumps(runs.record['report_contract_policy']))['failures'] == 5
    assert all(q['_id'] == 'run' and q['principal_id'] == 'p' and q['session_id'] == 's' and 'epoch' in q for q, _ in runs.writes)


def test_policy_persistence_rejects_stale_epoch_writes():
    from harness.store import LeaseLost
    async def handler(args, context):
        return ToolResult()
    runs = PersistentRuns()
    context = SimpleNamespace(run={'run_id': 'run', 'principal_id': 'p', 'session_id': 's', 'epoch': 0}, store=SimpleNamespace(runs=runs))
    with pytest.raises(LeaseLost):
        asyncio.run(setup_registry(handler).invoke('render_yield_table', {}, context))
    assert 'report_contract_policy' not in runs.record


def test_policy_write_fence_rejects_epoch_change_during_handler():
    from harness.store import LeaseLost
    runs = PersistentRuns()
    async def handler(args, context):
        runs.record['epoch'] = 2
        return ToolResult()
    context = SimpleNamespace(run={'run_id': 'run', 'principal_id': 'p', 'session_id': 's', 'epoch': 1}, store=SimpleNamespace(runs=runs))
    with pytest.raises(LeaseLost):
        asyncio.run(setup_registry(handler).invoke('render_yield_table', {'lot_cd': 'good'}, context))
    assert runs.writes[0][0]['epoch'] == 1
    assert 'report_contract_policy' not in runs.record


def test_policy_without_epoch_can_persist_with_full_ownership_filter():
    runs = PersistentRuns()
    async def handler(args, context):
        return ToolResult()
    context = SimpleNamespace(run={'run_id': 'run', 'principal_id': 'p', 'session_id': 's'}, store=SimpleNamespace(runs=runs))
    result = asyncio.run(setup_registry(handler).invoke('render_yield_table', {}, context))
    assert result.scope['count'] == 1
    assert runs.writes[0][0] == {'_id': 'run', 'principal_id': 'p', 'session_id': 's'}
