"""Deterministic renderer contract feedback and a run-local repair budget."""
from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
import copy
import hashlib
import json
import uuid
from typing import Any

from pydantic import ValidationError


REPORT_SCOPE_CONTRACT = 'yield-report-scope/v1'
LOT_POPULATION_PROJECTION = {
    'group_by': ['lot_id'],
    'aggregations': {'pt1h_mean': 'mean'},
    'units': {'pt1h_mean': 'fraction'},
    'preview_policy': 'read_result',
}


def legacy_report_projections(scope):
    """Recover the table contract when reading a report stored before projections were declared."""
    population = scope.get('lot_population')
    if isinstance(population, dict) and population.get('table_id'):
        return {population['table_id']: LOT_POPULATION_PROJECTION}
    if scope.get('contract') == REPORT_SCOPE_CONTRACT:
        return {'lot_population': LOT_POPULATION_PROJECTION}
    return {}


class ReportContractError(ValueError):
    def __init__(self, code: str, details: str | dict):
        self.code = code
        self.details = details
        super().__init__(str(details))


@dataclass
class ReportPolicyState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    restored: bool = False
    failures: int = 0
    invalid_arguments: dict[str, int] = field(default_factory=dict)
    successful_reports: list[dict] = field(default_factory=list)


def report_run_key(context):
    run = getattr(context, 'run', None) or {}
    if not run.get('run_id'):
        return None
    return tuple(run.get(key) for key in ('principal_id', 'session_id', 'run_id'))


LEGAL_SHAPES = [
    {'lot_cd': 'product code', 'period': {'kind': 'recent', 'weeks': 4}},
    {'report_scope_id': 'yield-report:<stored scope result id>'},
    {'report_scope_id': 'yield-report:<stored scope result id>', 'layout': 'group',
     'grouping': {'membership': {'kind': 'inline', 'groups': [{'group_id': 'A', 'lot_ids': ['LOT_ID']}]}}},
    {'report_scope_id': 'yield-report:<stored scope result id>', 'layout': 'group_period',
     'grouping': {'membership': {'kind': 'table', 'result_id': '<saved result id>', 'table_id': '<membership table id>'}}},
    {'lot_cd': 'product code', 'analysis': {key: 'requested count' for key in
        ('months', 'weeks', 'days', 'parameters_per_direction', 'bins_per_test')}},
]



def _contract_feedback(received, arguments, details):
    received_keys = sorted(received) if isinstance(received, dict) else []
    arguments = arguments if isinstance(arguments, dict) else {}
    missing = []
    if 'analysis' in arguments:
        feedback = dict(details) if isinstance(details, dict) else {'message': details}
        return {**feedback, 'received_keys': received_keys, 'missing': [] if arguments.get('lot_cd') else ['lot_cd'],
                'example': copy.deepcopy(LEGAL_SHAPES[-1])}
    if not arguments.get('report_scope_id') and not (arguments.get('lot_cd') and arguments.get('period')):
        missing.append('report_scope_id (or lot_cd+period)')
    group_required = (arguments.get('layout') in ('group', 'group_period')
                      or arguments.get('comparison') is not None
                      or arguments.get('grouping') is not None)
    grouping = arguments.get('grouping')
    if group_required and not (isinstance(grouping, dict) and grouping.get('membership')):
        missing.append('grouping.membership')
    paths = {key: f'grouping.{key}' for key in ('coverage', 'definition', 'groups', 'membership')}
    paths['member_grain'] = 'grouping.membership.member_grain'
    misplaced = {key: f'move into {path}' for key, path in paths.items() if key in received_keys}
    example = copy.deepcopy(LEGAL_SHAPES[2] if group_required or misplaced else LEGAL_SHAPES[1])
    if 'grouping' in example:
        example['grouping']['membership']['groups'] = [{'group_id': '<group id>', 'lot_ids': ['<lot id>']}]
    feedback = dict(details) if isinstance(details, dict) else {'message': details}
    if missing:
        message = ('Missing scope: pass report_scope_id from the earlier render_yield_table result (or lot_cd+period). '
                   if 'report_scope_id (or lot_cd+period)' in missing else '')
        if 'grouping.membership' in missing:
            message += 'Missing grouping.membership: pass identifier membership. '
        feedback.setdefault('message', message + f'Received keys: {received_keys}')
    return {**feedback, 'received_keys': received_keys, 'missing': missing,
            'misplaced': misplaced, 'example': example}


def _failure(state, fingerprint, code, details, *, record=True):
    from .registry import ToolResult

    if record:
        state.failures += 1
        state.invalid_arguments[fingerprint] = state.invalid_arguments.get(fingerprint, 0) + 1
    repeated = state.invalid_arguments.get(fingerprint, 0)
    if state.failures >= 5:
        code = 'report_budget_exhausted'
    elif repeated >= 3:
        code = 'repeated_invalid_report'
    message = details if isinstance(details, str) else json.dumps(details, ensure_ascii=False, default=str)
    summary = f'Report contract error: {code}. {message}'
    if len(summary) > 1200:
        summary = summary[:1199] + '…'
    sources = []
    for reference in state.successful_reports:
        sources.extend(reference.get('source_result_ids', []))
        if reference.get('result_id'):
            sources.append(reference['result_id'])
        if reference.get('membership', {}).get('result_id'):
            sources.append(reference['membership']['result_id'])
    return ToolResult(
        status='error', summary=summary, source_result_ids=list(dict.fromkeys(sources)),
        scope={
            'code': code, 'details': details,
            'retryable': state.failures < 5 and repeated < 3,
            'count': state.failures, 'repeated_count': repeated,
            'legal_shapes': LEGAL_SHAPES,
            'membership_hint': {
                'required_columns': ['group_id', 'lot_id'],
                'wafer_columns': ['group_id', 'lot_id', 'wf_id'],
                'member_grain': '기본 lot; wafer는 member_grain=wafer와 PRB WFID 원문 문자열 wf_id가 필요하다.',
                'rendering': 'query_db/run_python으로 멤버십 구성 후 공식 그룹 수율 표는 render_yield_table로 렌더링한다. 동일 OPER_ID의 wafer에 여러 EQP 행이면 임의 중복 제거 없이 clarify한다.',
                'reference': 'Use a saved result_id and table_id for complete identifier-only membership; do not send report cells or raw measurement rows.',
                'table_selection': 'table_id may be omitted only when the result contains exactly one table overall and that table has group_id and lot_id.',
            },
            'successful_reports': list(state.successful_reports),
        },
    )


async def invoke_report(spec, arguments: Any, context, *, state=None):
    """Validate and invoke only the report tool; transient failures propagate."""
    state = state if state is not None else ReportPolicyState()
    async with state.lock:
        runs = getattr(getattr(context, 'store', None), 'runs', None)
        run = getattr(context, 'run', None) or {}
        if runs is not None and report_run_key(context) is not None:
            key = {'_id': run['run_id'], 'principal_id': run.get('principal_id'), 'session_id': run.get('session_id')}
            if not state.restored:
                saved_run = await runs.find_one(key)
                if saved_run is not None:
                    if 'epoch' in run and saved_run.get('epoch') != run['epoch']:
                        from ..store import LeaseLost
                        raise LeaseLost('Report policy run epoch changed')
                    saved = saved_run.get('report_contract_policy') or {}
                    state.failures = saved.get('failures', state.failures)
                    state.invalid_arguments = copy.deepcopy(saved.get('invalid_arguments', state.invalid_arguments))
                    state.successful_reports = copy.deepcopy(saved.get('successful_reports', state.successful_reports))
                state.restored = True
            result = await _invoke_report(spec, arguments, context, state)
            if 'epoch' in run:
                key['epoch'] = run['epoch']
            saved = {
                'failures': state.failures,
                'invalid_arguments': dict(state.invalid_arguments),
                'successful_reports': copy.deepcopy(state.successful_reports),
            }
            updated = await runs.update_one(key, {'$set': {'report_contract_policy': saved}})
            if getattr(updated, 'matched_count', 1) == 0:
                from ..store import LeaseLost
                raise LeaseLost('Report policy run ownership or epoch changed')
            return result
        return await _invoke_report(spec, arguments, context, state)


async def _invoke_report(spec, arguments, context, state):
    fingerprint = hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()
    if state.failures >= 5:
        # Exhaustion is stable: later calls do not execute validation or the handler.
        return _failure(state, fingerprint, 'report_budget_exhausted',
                        'The five-failure report repair budget is exhausted for this run.', record=False)
    if state.invalid_arguments.get(fingerprint, 0) >= 2:
        return _failure(state, fingerprint, 'repeated_invalid_report', 'Unchanged invalid arguments were repeated; change the request shape before retrying.')
    received = arguments
    autofilled_scope = None
    if (isinstance(arguments, dict) and arguments.get('grouping')
            and not any(arguments.get(key) for key in ('report_scope_id', 'lot_cd', 'period'))):
        autofilled_scope = next((report['report_scope_id'] for report in reversed(state.successful_reports)
                                 if report.get('report_scope_id')), None)
        if autofilled_scope:
            arguments = {**arguments, 'report_scope_id': autofilled_scope}
            # Empty scope selectors are absent, including empty strings/dicts.
            arguments.pop('lot_cd', None)
            arguments.pop('period', None)
    try:
        args = spec.schema.model_validate(arguments)
    except ValidationError as exc:
        details = [{'location': list(error['loc']), 'type': error['type'], 'msg': error['msg']} for error in exc.errors(include_input=False, include_context=False, include_url=False)]
        return _failure(state, fingerprint, 'invalid_report_schema',
                        _contract_feedback(received, arguments, {'errors': details}))
    try:
        result = await spec.handler(args, context)
    except ReportContractError as exc:
        return _failure(state, fingerprint, exc.code, _contract_feedback(received, arguments, exc.details))
    if result.status == 'error' and result.scope.get('code'):
        return _failure(state, fingerprint, result.scope['code'],
                        _contract_feedback(received, arguments, result.scope.get('details', result.summary)))
    if result.status != 'error':
        if autofilled_scope:
            result.scope['report_scope_autofilled'] = True
            result.scope['report_scope_autofilled_from'] = autofilled_scope
            result.summary += f'; report_scope_id auto-filled from latest scope in this session: {autofilled_scope}'
        reference = {key: result.scope[key] for key in ('report_scope_id', 'membership_result_id', 'membership_table_id') if key in result.scope}
        membership = result.scope.get('membership')
        if isinstance(membership, dict):
            reference['membership'] = {key: membership[key] for key in ('kind', 'result_id', 'table_id') if key in membership}
        run = getattr(context, 'run', None) or {}
        invocation_id = getattr(context, 'invocation_id', None)
        if run.get('run_id') and invocation_id:
            reference['result_id'] = str(uuid.uuid5(uuid.NAMESPACE_URL, run['run_id'] + ':' + invocation_id))
        if result.source_result_ids:
            reference['source_result_ids'] = list(result.source_result_ids)
        if reference and reference not in state.successful_reports:
            state.successful_reports.append(reference)
    return result
