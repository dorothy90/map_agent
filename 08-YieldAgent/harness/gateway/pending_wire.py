"""Hermes 5894e750 pending contracts; no transport, tools or database access.

Host-only shapes (event descriptors, audit, timeout budget) have no upstream
counterpart: PORTING_BASELINE:391,517,607,663,731 / PR-6a decisions.
"""
from copy import deepcopy
import hashlib
import json
import math
import time

from .pending_redaction import redact


class PendingControlError(ValueError):
    """Host equivalent of hermes_state_runtime.RuntimeStoreError('invalid_params')."""
    code = 'invalid_params'

    def __init__(self):
        super().__init__(self.code)


def effective_timeout(default, remaining_seconds):
    return max(0, min(default, remaining_seconds - 1))


def validate_generation(generation):
    if type(generation) is not int or generation < 0:
        raise PendingControlError()


def validate_timeout(timeout):
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                or not math.isfinite(timeout) or timeout < 0):
        raise PendingControlError()


def authority_choices(data):
    # H/gateway/session_pending_controls.py:20-25 (independent allow_permanent).
    choices = ['once', 'deny']
    if data.get('allow_session', True) and not data.get('smart_denied', False):
        choices.append('session')
    if data.get('allow_permanent', True) and not data.get('smart_denied', False):
        choices.append('always')
    return ['once', 'deny'] if 'edit' in data else choices


def legacy_choices(data):
    # H/tui_gateway/server.py:781-793 (always nested inside allow_session).
    if 'choices' in data:
        return list(data['choices'])
    choices = ['once']
    if not data.get('smart_denied') and data.get('allow_session') is not False:
        choices.append('session')
        if data.get('allow_permanent') is not False:
            choices.append('always')
    return choices + ['deny']


def approval_prompt(generation, data, *, timeout_s=None):
    validate_generation(generation)
    validate_timeout(timeout_s)
    prompt = {'kind': 'approval', 'prompt_id': data['request_id'],
              'execution_generation': generation, 'command': redact(data.get('command', '')),
              'description': redact(data.get('description', '')), 'choices': authority_choices(data)}
    if 'edit' in data:
        prompt['edit'] = deepcopy(data['edit'])
    if timeout_s is not None:
        prompt['timeout_s'] = timeout_s
    return prompt


def normalize_questions(questions):
    if not isinstance(questions, list) or not 1 <= len(questions) <= 5:
        raise PendingControlError()
    normalized, seen = [], set()
    for q in questions:
        if (not isinstance(q, dict) or not isinstance(q.get('qid'), str) or not q['qid']
                or q['qid'] in seen or not isinstance(q.get('question'), str)
                or type(q.get('multi_select', False)) is not bool):
            raise PendingControlError()
        choices = q.get('choices')
        if choices is not None and (not isinstance(choices, list) or any(not isinstance(c, str) for c in choices)):
            raise PendingControlError()
        seen.add(q['qid'])
        normalized.append({'qid': q['qid'], 'question': redact(q['question']),
                           'choices': [redact(c) for c in choices] if choices is not None else None,
                           'multi_select': q.get('multi_select', False)})
    return normalized


def question_prompt_id(prompt_id, questions, index):
    # No upstream batch→authority identity mapping: PB:517 same pending link.
    return prompt_id if len(questions) == 1 else f'{prompt_id}:q{index + 1}'


def clarify_prompts(prompt_id, generation, questions, answers=None, *, timeout_s=None):
    # H/gateway/session_pending_controls.py:40-51; canonical_sessions.py:98-110.
    validate_generation(generation)
    validate_timeout(timeout_s)
    result = []
    for index, q in enumerate(normalize_questions(questions)):
        if q['qid'] in (answers or {}):
            continue
        prompt = {'kind': 'clarify', 'prompt_id': question_prompt_id(prompt_id, questions, index),
                  'execution_generation': generation, 'question': q['question'],
                  'choices': q['choices'] or [], 'multi_select': q['multi_select']}
        if timeout_s is not None:
            prompt['timeout_s'] = timeout_s
        result.append(prompt)
    return result


def validate_response(prompt, response, kind):
    # H/gateway/session_pending_controls.py:72-80 remote responder contract.
    field = 'answer' if kind == 'clarify' else 'choice'
    if (kind not in ('clarify', 'approval') or prompt['kind'] != kind
            or not isinstance(response, dict) or set(response) != {field}
            or not isinstance(response[field], str) or len(response[field]) > 16384
            or (kind == 'approval' and response[field] not in prompt['choices'])):
        raise PendingControlError()
    return response[field]


def answer_for_prompt(prompt_id, questions, authority_id, answer):
    for index, q in enumerate(questions):
        if authority_id == question_prompt_id(prompt_id, questions, index):
            # canonical.py:367: empty = skipped; clarify_tool.py:87-92.
            return {q['qid']: answer or None}
    raise PendingControlError()


def build_request_event(session_id, prompt):
    return {'type': prompt['kind'] + '.request', 'session_id': session_id, 'payload': deepcopy(prompt)}


def build_settled_event(session_id, prompt):
    return {'type': prompt['kind'] + '.settled', 'session_id': session_id, 'payload': {
        'prompt_id': prompt['prompt_id'], 'execution_generation': prompt['execution_generation']}}


def build_cancelled_event(session_id, stored_session_id, reason, request_ids):
    # H/tui_gateway/contracts/events.py:546-563.
    return {'type': 'approval.cancelled', 'session_id': session_id, 'payload': {
        'session_id': session_id, 'stored_session_id': stored_session_id, 'reason': reason,
        'cancelled_count': len(request_ids), 'request_ids': list(request_ids)}}


def legacy_approval_request(session_id, request_id, data):
    # Only declared keys cross this boundary; no arbitrary queue metadata/secrets.
    keys = ('request_id', 'allow_session', 'allow_permanent', 'smart_denied', 'tool_name', 'gateway_session_id')
    params = {k: deepcopy(data[k]) for k in keys if k in data}
    params.update(session_id=session_id, command=redact(data.get('command', '')),
                  description=redact(data.get('description', '')), choices=legacy_choices(data))
    return {'jsonrpc': '2.0', 'id': request_id, 'method': 'approval', 'params': params}


def legacy_clarify_request(session_id, request_id, questions, *, answers=None):
    # H/tui_gateway/server_requests.py:91-102 replay locks (including null).
    params = {'session_id': session_id, 'questions': normalize_questions(questions)}
    if answers is not None:
        params['answers'] = deepcopy(answers)
    return {'jsonrpc': '2.0', 'id': request_id, 'method': 'clarify', 'params': params}


def parse_approval_result(result, choices):
    if (not isinstance(result, dict) or set(result) - {'choice', 'all'}
            or not isinstance(result.get('choice'), str) or result['choice'] not in choices
            or result['choice'] not in ('once', 'session', 'always', 'deny')
            or ('all' in result and result['all'] is not None and type(result['all']) is not bool)):
        raise PendingControlError()
    return deepcopy(result)


def parse_clarify_result(result, questions, locked=None):
    # H/contracts/server_requests.py:49-56 + tools/clarify_batch.py:17-35.
    if not isinstance(result, dict) or set(result) - {'answers', 'outcome'}:
        raise PendingControlError()
    if result.get('outcome') not in (None, 'submitted', 'timed_out', 'cancelled'):
        raise PendingControlError()
    answers = result.get('answers')
    if answers is None:
        return {'answers': dict(locked or {}), 'outcome': 'cancelled'}
    qids = {q['qid'] for q in questions}
    if (not isinstance(answers, dict) or set(answers) - qids
            or any(v is not None and (not isinstance(v, str) or len(v) > 16384) for v in answers.values())):
        raise PendingControlError()
    return {'answers': {**(locked or {}), **answers}, 'outcome': result.get('outcome') or 'submitted'}


def rule_key(tool_name, reason):
    # H/tools/approval.py:1132-1135, including the empty-reason fallback.
    description = reason or f'Plugin requires approval for {tool_name}'
    return tool_name + ':' + hashlib.sha256(description.encode('utf-8')).hexdigest()[:12]


def audit_record(principal_id, event, *, kind=None, prompt_id=None, run_id=None,
                 session_id=None, tool_name=None, decision=None, bodies=None, ts=None):
    # No upstream host persistence: PB:391,607,731. Whitelist, never **metadata.
    record = dict(principal_id=principal_id, event=event, kind=kind, prompt_id=prompt_id,
                  run_id=run_id, session_id=session_id, tool_name=tool_name, decision=decision,
                  ts=time.time() if ts is None else ts)
    for key in ('question', 'command', 'description', 'answer', 'result', 'reason'):
        if key in (bodies or {}):
            value = bodies[key]
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
            record[key + '_chars'] = len(text)
            record[key + '_sha256_prefix'] = hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]
    return record


# H/contracts/server_requests.py:59,159,195,212-357; display.py:167.
# PB:375,640: typed empty ValueResult or -32601; never window-only 4404.
SERVER_REQUEST_POLICY = {
    'clarify': 'handled', 'approval': 'handled', 'setup_beat': 'method_not_found',
    'sudo': 'value', 'secret': 'value', 'vault.unlock_prompt': 'value',
    'vault.save_login': 'value', 'vault.code': 'value', 'terminal.read': 'value',
    'preview.read': 'value', 'window.read': 'value', 'preview.act': 'value',
    'tour': 'value', 'theme.apply': 'value', 'theme.pick': 'method_not_found',
    'display.install.sudo': 'value',
}


def decline_server_request(request_name, params):
    """No params inspection or logging; None means approval/clarify dispatch only."""
    policy = SERVER_REQUEST_POLICY.get(request_name, 'method_not_found')
    if policy == 'handled':
        return None
    if policy == 'value':
        return {'value': ''}
    return {'error': {'code': -32601, 'message': 'Method not found'}}
