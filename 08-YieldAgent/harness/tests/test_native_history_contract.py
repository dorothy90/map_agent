from copy import deepcopy

from harness.native_verification import final_messages


def test_rewritten_answer_drops_replay_sidecar_without_mutating_input():
    messages = [{'role': 'user', 'content': 'question'},
        {'role': 'assistant', 'content': '', 'api_content': 'rejected draft'}]
    original = deepcopy(messages)
    clean = final_messages(messages, [], 'verification failed')
    assert clean[-1] == {'role': 'assistant', 'content': 'verification failed'}
    assert messages == original


def test_missing_turn_boundary_preserves_history_and_repeated_current_question():
    history = [{'role': 'user', 'content': 'same question'},
        {'role': 'assistant', 'content': 'previous correct answer'}]
    clean = final_messages(history, [], 'compression failed',
        conversation_history=history, user_message='same question')
    assert clean == history + [{'role': 'user', 'content': 'same question'},
        {'role': 'assistant', 'content': 'compression failed'}]


def test_proven_compacted_boundary_preserves_prior_rows_and_tool_evidence():
    messages = [{'role': 'user', 'content': 'summary'},
        {'role': 'assistant', 'content': 'historical answer', 'api_content': 'historical wire'},
        {'role': 'user', 'content': 'current question'},
        {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'call'}]},
        {'role': 'tool', 'tool_call_id': 'call', 'content': 'evidence'},
        {'role': 'assistant', 'content': 'draft', 'api_content': 'draft wire'}]
    clean = final_messages(messages, [], 'verified', turn_id='turn-2',
        current_turn_user_idx=2, conversation_history=[], user_message='current question')
    assert clean[:-1] == messages[:-1]
    assert clean[-1] == {'role': 'assistant', 'content': 'verified'}


def test_invalid_boundary_does_not_claim_a_historical_assistant():
    history = [{'role': 'user', 'content': 'old question'},
        {'role': 'assistant', 'content': 'old answer'}]
    clean = final_messages(history, [], 'failed', turn_id='turn-2',
        current_turn_user_idx=1, conversation_history=history, user_message='new question')
    assert clean == history + [{'role': 'user', 'content': 'new question'},
        {'role': 'assistant', 'content': 'failed'}]


def test_upstream_boundary_and_replay_contract_in_isolated_interpreter(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys

    harness = Path(__file__).resolve().parents[1]
    code = '''
import sys
from types import SimpleNamespace
sys.path[:0] = [sys.argv[1] + '/engine', sys.argv[1]]
from agent.turn_context import export_current_turn_boundary, substitute_api_content
from native_verification import final_messages
history = [{'role': 'user', 'content': 'same question'},
           {'role': 'assistant', 'content': 'previous answer'}]
agent = SimpleNamespace(_current_turn_id='new-turn')
timeout = export_current_turn_boundary(agent, {'messages': history,
    'turn_exit_reason': 'context_compression_timeout'}, 'same question')
assert 'current_turn_user_idx' not in timeout
settled = final_messages(timeout['messages'], [], 'timeout',
    turn_id=timeout.get('turn_id'), current_turn_user_idx=timeout.get('current_turn_user_idx'),
    conversation_history=history, user_message='same question')
assert settled[:2] == history and settled[2]['content'] == 'same question'
result = export_current_turn_boundary(agent, {'messages': history + [
    {'role': 'user', 'content': 'same question'},
    {'role': 'assistant', 'content': '', 'api_content': 'rejected draft'}]}, 'same question')
assert result['current_turn_user_idx'] == 2
settled = final_messages(result['messages'], [], 'verification failed',
    turn_id=result['turn_id'], current_turn_user_idx=result['current_turn_user_idx'],
    conversation_history=history, user_message='same question')
wire = dict(settled[-1])
substitute_api_content(wire)
assert wire['content'] == 'verification failed'
assert settled[:2] == history
'''
    env = {**os.environ, 'HERMES_HOME': str(tmp_path / 'profile'),
        'HERMES_DISABLE_LAZY_INSTALLS': '1'}
    result = subprocess.run([sys.executable, '-B', '-c', code, str(harness)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
