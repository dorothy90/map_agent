"""PR-6a pure contracts only: no database, worker, sockets or model calls."""
import hashlib
import json

import pytest

from harness.gateway import pending_wire as w


def test_two_choice_builders_are_deliberately_different():
    assert w.authority_choices({}) == ['once', 'deny', 'session', 'always']
    assert w.legacy_choices({}) == ['once', 'session', 'always', 'deny']
    assert w.authority_choices({'allow_session': False}) == ['once', 'deny', 'always']
    assert w.legacy_choices({'allow_session': False}) == ['once', 'deny']
    for build in (w.authority_choices, w.legacy_choices):
        assert build({'smart_denied': True}) == ['once', 'deny']
    assert w.legacy_choices({'choices': ['deny']}) == ['deny']


def test_approval_payload_and_events():
    data = {'request_id': 'p', 'command': 'safe', 'description': 'why', 'tool_name': 'synthetic'}
    prompt = w.approval_prompt(7, data, timeout_s=30)
    assert prompt == dict(kind='approval', prompt_id='p', execution_generation=7,
                         command='safe', description='why', choices=['once', 'deny', 'session', 'always'], timeout_s=30)
    assert w.build_request_event('s', prompt) == {'type': 'approval.request', 'session_id': 's', 'payload': prompt}
    assert w.build_settled_event('s', prompt)['payload'] == {'prompt_id': 'p', 'execution_generation': 7}
    frame = w.legacy_approval_request('s', 'srq-1', data)
    assert frame['jsonrpc'] == '2.0' and frame['method'] == 'approval'
    assert frame['params']['choices'] == ['once', 'session', 'always', 'deny']
    assert frame['params']['request_id'] == 'p'
    assert w.build_cancelled_event('s', 'stored', 'interrupt', ['p']) == {
        'type': 'approval.cancelled', 'session_id': 's', 'payload': {
            'session_id': 's', 'stored_session_id': 'stored', 'reason': 'interrupt',
            'cancelled_count': 1, 'request_ids': ['p']}}


@pytest.mark.parametrize('response,kind', [({}, 'approval'), ({'choice': 'bad'}, 'approval'),
    ({'choice': 'once', 'all': True}, 'approval'), ({'choice': []}, 'approval'),
    ({'answer': 'a'}, 'clarify'), (None, 'approval')])
def test_authority_invalid_params(response, kind):
    with pytest.raises(w.PendingControlError, match='invalid_params'):
        w.validate_response({'kind': 'approval', 'choices': ['once', 'deny']}, response, kind)


def test_answer_limit_and_kind():
    p = {'kind': 'clarify', 'choices': []}
    assert w.validate_response(p, {'answer': 'x' * 16384}, 'clarify') == 'x' * 16384
    assert w.validate_response(p, {'answer': ''}, 'clarify') == ''
    for response in ({'answer': 'x' * 16385}, {'answer': None}, {'answer': 'x', 'extra': 1}):
        with pytest.raises(w.PendingControlError):
            w.validate_response(p, response, 'clarify')


def test_legacy_parsers_and_batch_replay():
    questions = [{'qid': 'q1', 'question': 'First', 'choices': None, 'multi_select': False},
                 {'qid': 'q2', 'question': 'Second', 'choices': ['A', 'B'], 'multi_select': True}]
    frame = w.legacy_clarify_request('s', 'p', questions, answers={'q1': None})
    assert frame == {'jsonrpc': '2.0', 'id': 'p', 'method': 'clarify', 'params': {
        'session_id': 's', 'questions': questions, 'answers': {'q1': None}}}
    prompts = w.clarify_prompts('p', 3, questions, {'q1': None}, timeout_s=300)
    assert len(prompts) == 1 and prompts[0]['question'] == 'Second'
    assert prompts[0]['kind'] == 'clarify' and prompts[0]['multi_select'] is True
    assert w.answer_for_prompt('p', questions, prompts[0]['prompt_id'], 'A, B') == {'q2': 'A, B'}
    assert w.parse_clarify_result({'answers': {'q2': 'A'}}, questions, {'q1': None}) == {
        'answers': {'q1': None, 'q2': 'A'}, 'outcome': 'submitted'}
    assert w.parse_clarify_result({}, questions, {'q1': 'yes'}) == {'answers': {'q1': 'yes'}, 'outcome': 'cancelled'}
    assert w.parse_approval_result({'choice': 'once', 'all': True}, ['once']) == {'choice': 'once', 'all': True}
    for result in ({'choice': 'session'}, {'choice': 'once', 'all': 'yes'}):
        with pytest.raises(w.PendingControlError):
            w.parse_approval_result(result, ['once'])
    with pytest.raises(w.PendingControlError):
        w.parse_clarify_result({'answers': {'unknown': 'x'}}, questions)


def test_rule_key_timeout_and_audit_without_bodies():
    assert w.rule_key('tool', 'reason') == 'tool:' + hashlib.sha256(b'reason').hexdigest()[:12]
    assert w.effective_timeout(300, 20) == 19
    assert w.effective_timeout(300, .5) == 0
    assert w.effective_timeout(10, 40) == 10
    row = w.audit_record('a', 'response', kind='clarify', prompt_id='p', run_id='r',
                         session_id='s', bodies={'answer': 'PRIVATE BODY', 'result': {'x': 'PRIVATE BODY'}})
    assert row['answer_chars'] == 12 and len(row['answer_sha256_prefix']) == 12
    assert 'PRIVATE BODY' not in json.dumps(row)
    assert 'answer' not in row and 'result' not in row


def test_decline_inventory():
    assert len(w.SERVER_REQUEST_POLICY) == 16
    for name, policy in w.SERVER_REQUEST_POLICY.items():
        result = w.decline_server_request(name, {'value': 'MUST NOT COPY'})
        if policy == 'handled':
            assert result is None
        elif policy == 'value':
            assert result == {'value': ''}
        else:
            assert result == {'error': {'code': -32601, 'message': 'Method not found'}}
    for name in ('sudo', 'secret', 'vault.unlock_prompt', 'display.install.sudo'):
        assert w.decline_server_request(name, {}) == {'value': ''}
    assert w.decline_server_request('unknown', {})['error']['code'] == -32601


def test_masking_uses_engine_and_preserves_plain_text():
    assert w.redact('plain text') == 'plain text'
    assert w.redact(None) == ''
    synthetic = 'password=example-private-value'
    assert w.redact(synthetic) != synthetic
    data = {'request_id': 'p', 'command': synthetic, 'description': synthetic}
    assert synthetic not in json.dumps(w.approval_prompt(1, data))
    assert synthetic not in json.dumps(w.legacy_approval_request('s', 'p', data))
    assert synthetic not in json.dumps(w.clarify_prompts('p', 1, [{'qid': 'q', 'question': synthetic}]))


@pytest.mark.parametrize('questions', [[], [{'qid': 'q', 'question': 1}],
    [{'qid': 'q', 'question': 'Q', 'choices': 'bad'}],
    [{'qid': 'q', 'question': 'Q'}, {'qid': 'q', 'question': 'duplicate'}]])
def test_invalid_questions(questions):
    with pytest.raises(w.PendingControlError):
        w.clarify_prompts('p', 1, questions)


@pytest.mark.parametrize('value', [True, -1, 1.5, '1'])
def test_invalid_generation(value):
    with pytest.raises(w.PendingControlError):
        w.approval_prompt(value, {'request_id': 'p'})


def test_legacy_cancellation_timeout_and_single_question_skip():
    questions = [{'qid': 'q', 'question': 'Question'}]
    assert w.answer_for_prompt('p', questions, 'p', '') == {'q': None}
    assert w.parse_clarify_result({'answers': {}, 'outcome': 'timed_out'}, questions, {'q': 'locked'}) == {
        'answers': {'q': 'locked'}, 'outcome': 'timed_out'}
    assert w.parse_clarify_result({'answers': None}, questions)['outcome'] == 'cancelled'
    for result in ({'answers': []}, {'answers': {'q': 1}}, {'answers': {'q': 'x' * 16385}},
                   {'outcome': 'unknown'}, {'unexpected': 'value'}):
        with pytest.raises(w.PendingControlError):
            w.parse_clarify_result(result, questions)
    with pytest.raises(w.PendingControlError):
        w.parse_approval_result({'choice': 'injected'}, ['injected'])


def test_store_import_and_mongo_test_syntax_without_running_mongo():
    import ast
    from pathlib import Path
    from harness.gateway.pending_controls import PendingControls
    assert PendingControls.POLL_SECONDS <= .1
    ast.parse(Path(__file__).with_name('test_pending_controls_mongo.py').read_text())


def test_redaction_does_not_modify_host_import_namespace():
    import sys
    before = {key: sys.modules.get(key) for key in ('agent', 'agent.redact', 'agent.file_safety', 'hermes_constants')}
    w.redact('safe')
    assert before == {key: sys.modules.get(key) for key in before}


def test_rule_empty_reason_matches_upstream_fallback():
    assert w.rule_key('synthetic', '') == 'synthetic:' + hashlib.sha256(
        b'Plugin requires approval for synthetic').hexdigest()[:12]
