import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
from pydantic import ValidationError

from harness.config import Settings
from harness.map_intent import interpret_map_intent
from harness.native_budget import install_http_budget


def resolve(monkeypatch, response, history=None, query='4SS로 변경', previous=None, finish='stop', memories=None, oper=None):
    sent = []
    targets = {} if memories else {'target_lot_ids': None, 'target_wf_ids': None}
    response = {'evidence': [], 'unresolved': [], **targets, **response}
    if 'action' in response:
        response['transition'] = response.pop('action') + '_' + response.pop('kind')

    def endpoint(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={'id': 'selection', 'object': 'chat.completion',
            'created': 0, 'model': 'test', 'choices': [{'index': 0, 'finish_reason': finish,
            'message': {'role': 'assistant', 'content': json.dumps(response)}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}})

    from harness.tools import load_wafer_maps
    monkeypatch.setattr(load_wafer_maps, 'get_map_dimensions',
                        AsyncMock(return_value=[('4SA', 'PT1H TEST'), ('4SS', 'PT1H TEST')]), raising=False)
    configured_client = openai.AsyncOpenAI
    monkeypatch.setattr(openai, 'AsyncOpenAI', lambda **kw: configured_client(
        **kw, http_client=httpx.AsyncClient(transport=httpx.MockTransport(endpoint))))
    settings = Settings(model='test', api_key='local', base_url='https://offline/v1')
    ctx = SimpleNamespace(check=AsyncMock(), remaining_seconds=lambda: 10, settings=settings,
                          run={'query': query})
    budget = install_http_budget(model_limit=2, token_limit=100000, max_output_tokens=4096,
                                 base_url=settings.base_url)
    try:
        result = asyncio.run(interpret_map_intent(ctx, previous, history or [], memories, oper))
        assert budget.snapshot()['models'] == 1
        assert budget.snapshot()['tokens'] == 120
        ctx.check.assert_awaited_once()
        return result, sent[0]
    finally:
        budget.close()


@pytest.mark.parametrize('invalid', [None, 'cross_lot', 'forgot_group', 'unknown_ref'])
def test_memory_selection_requires_exact_members_from_each_cited_entry(monkeypatch, invalid):
    sources = {'m1': 'Alpha (4SS) is LOT-X wafers 03, 11.', 'm2': 'Beta (4SS) is LOT-Y wafers 03, 19.'}
    groups = [dict(memory_ref='m1', label='Alpha', lot_id='LOT-X', wf_ids=[3, 11],
                   membership_quote='LOT-X wafers 03, 11'),
              dict(memory_ref='m2', label='Beta', lot_id='LOT-Y', wf_ids=[3, 19],
                   membership_quote='LOT-Y wafers 03, 19')]
    if invalid == 'cross_lot':
        groups[0]['wf_ids'] = [3, 19]
    if invalid == 'forgot_group':
        groups.pop()
    if invalid == 'unknown_ref':
        groups[0]['memory_ref'] = 'm3'
    response = dict(action='start', kind='targeted', lot_cd='4SS', oper='PT1H TEST', period=None, wafer_groups=groups,
                    evidence=[{'field': 'oper', 'quote': 'PT1H'}])
    def run():
        return resolve(monkeypatch, response, query='Show Alpha and Beta at PT1H', memories=sources)
    if invalid:
        with pytest.raises(ValueError, match='MAP_SCOPE_CONFLICT'):
            run()
    else:
        selection, request = run()
        assert selection['wafer_groups'] == [{k:g[k] for k in ('label', 'lot_id', 'wf_ids')} for g in groups]
        assert json.loads(request['messages'][-1]['content'])['selected_memory_entries'] == sources
        fields = request['response_format']['json_schema']['schema']['properties']
        assert 'wafer_groups' in fields and not {'target_wf_ids', 'target_lot_ids'} & fields.keys()
        assert selection['oper'] == 'PT1H TEST'


@pytest.mark.parametrize('query', ['same maps', 'use OTHER-STAGE'])
def test_stage_alias_requires_evidence_in_the_current_user_selection(monkeypatch, query):
    memory = 'Set A: LOT-A wafers 3, STAGE'
    group = dict(memory_ref='m', label='Set A', lot_id='LOT-A', wf_ids=[3], membership_quote='LOT-A wafers 3')
    response = dict(action='start', kind='targeted', lot_cd='4SS', oper='PT1H TEST', period=None,
                    wafer_groups=[group], evidence=[{'field': 'oper', 'quote': 'STAGE'}])
    with pytest.raises(ValueError, match='no current user selection for oper'):
        resolve(monkeypatch, response, history=[{'role': 'user', 'content': 'use STAGE'}],
                query=query, memories={'m': memory})


@pytest.mark.parametrize('stage', ['PT1H', 'PT1C'])
def test_echoed_stage_is_verified_against_user_and_database_without_a_second_quote(monkeypatch, stage):
    memory = {'m': 'A: LOT-A wafers 3'}
    group = dict(memory_ref='m', label='A', lot_id='LOT-A', wf_ids=[3], membership_quote='LOT-A wafers 3')
    response = dict(action='start', kind='targeted', lot_cd='4SS', oper='PT1H TEST', period=None, wafer_groups=[group])
    def select():
        return resolve(monkeypatch, response, query=f'A {stage} maps', memories=memory, oper='PT1H')
    if stage == 'PT1H':
        selection, _ = select()
        assert selection['oper'] == 'PT1H TEST'
    else:
        with pytest.raises(ValueError, match='no current user selection'):
            select()


def test_interpreter_sees_user_evidence_and_charges_once(monkeypatch):
    history = [{'role': 'tool', 'content': 'PRIVATE TABLE'},
               {'role': 'assistant', 'content': 'Which product?', 'reasoning': 'PRIVATE REASONING'},
               {'role': 'user', 'content': '4SS로 변경', 'current_turn': True}]
    previous = {'kind': 'scoped', 'lot_cd': '4SA', 'oper': 'PT1H TEST',
                'period': {'kind': 'recent_weeks', 'count': 4}, 'run_id': 'private-id'}
    response = {'action': 'change', 'kind': 'scoped', 'lot_cd': '4SS', 'oper': None, 'period': None,
                'evidence': [{'field': 'lot_cd', 'quote': '4SS'}]}
    result, request = resolve(monkeypatch, response, history, previous=previous)
    assert result == {'action': 'change', 'kind': 'scoped', 'lot_cd': '4SS'}
    assert request['response_format']['type'] == 'json_schema'
    properties = request['response_format']['json_schema']['schema']['properties']
    assert properties['lot_cd']['anyOf'][0]['enum'] == ['4SA', '4SS']
    payload = request['messages'][-1]['content']
    assert payload.count('4SS로 변경') == 1
    assert all(private not in payload for private in ('private-id', 'PRIVATE TABLE', 'PRIVATE REASONING'))
    assert json.loads(payload)['previous_selection']['period'] == previous['period']


@pytest.mark.parametrize('period', [{'kind': 'recent_weeks', 'count': 0},
    {'kind': 'range', 'start_time': '2030-12-01', 'end_time': '2030-11-10'},
    {'kind': 'all', 'implicit': True}])
def test_invalid_interpretation_cannot_select_all_dates(monkeypatch, period):
    with pytest.raises(ValidationError):
        resolve(monkeypatch, {'action': 'start', 'kind': 'scoped', 'lot_cd': None,
                'oper': None, 'period': period})


def test_scoped_continuation_cannot_become_a_targeted_lookup(monkeypatch):
    with pytest.raises(ValueError, match='invalid change'):
        resolve(monkeypatch, {'action': 'continue', 'kind': 'targeted', 'lot_cd': None,
                'oper': None, 'period': None}, previous={'kind': 'scoped'})


def test_missing_period_stays_unresolved(monkeypatch):
    result, _ = resolve(monkeypatch, {'action': 'start', 'kind': 'scoped',
        'lot_cd': '4SA', 'oper': 'PT1H TEST', 'period': None})
    assert 'period' not in result


def test_partial_model_reply_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match='selection'):
        resolve(monkeypatch, {}, finish='length')


def test_uncertain_replacement_cannot_inherit_an_old_window(monkeypatch):
    with pytest.raises(ValueError, match='Clarification required.*period'):
        resolve(monkeypatch, {'action': 'change', 'kind': 'scoped', 'lot_cd': '4SS',
                'oper': None, 'period': None, 'unresolved': ['period']},
                previous={'kind': 'scoped', 'period': {'kind': 'recent_weeks', 'count': 4}})


def test_provider_cannot_use_a_lot_identifier_as_a_product(monkeypatch):
    with pytest.raises(ValueError, match='outside the map catalog'):
        resolve(monkeypatch, {'action': 'start', 'kind': 'scoped', 'lot_cd': 'arbitrary-lot-id',
                'oper': 'PT1H TEST', 'period': {'kind': 'recent_weeks', 'count': 4}})


@pytest.mark.parametrize(('name', 'valid'), [('DEMO-AX', True), ('AX', False), ('DEMO-A', False)])
def test_target_identifier_must_be_complete_in_source(monkeypatch, name, valid):
    response = {'action': 'start', 'kind': 'targeted', 'target_lot_ids': [name],
                'target_wf_ids': [8], 'lot_cd': None, 'oper': None, 'period': None}
    if valid:
        result, _ = resolve(monkeypatch, response, query='DEMO-AX의 8번 웨이퍼 맵')
        assert result['target_lot_ids'] == [name] and result['target_wf_ids'] == [8]
    else:
        with pytest.raises(ValueError, match='complete target lot identifier'):
            resolve(monkeypatch, response, query='DEMO-AX의 8번 웨이퍼 맵')

def test_new_target_does_not_inherit_old_test_from_previous_selection(monkeypatch):
    response = {'action': 'start', 'kind': 'targeted', 'target_lot_ids': ['DEMO-AX'],
                'target_wf_ids': [8], 'lot_cd': None, 'oper': 'PT1H TEST', 'period': None,
                'evidence': [{'field': 'oper', 'quote': 'PT1H'}]}
    with pytest.raises(ValueError, match='no current user selection for oper'):
        resolve(monkeypatch, response, query='새 조회로 DEMO-AX 8번 맵',
                previous={'kind': 'scoped', 'lot_cd': '4SA', 'oper': 'PT1H TEST'})
